"""Incremental ingest: once a station has stored history, ingest_mountain must
fetch only the recent tail (from latest-stored-date - INGEST_OVERLAP_DAYS), and
an empty station -- first run, or a lost DB cache -- must still self-heal with a
full period-of-record pull. These lock in the behavior that keeps the daily
Action from re-pulling decades of data every run.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3

import numpy as np

from config import INGEST_OVERLAP_DAYS  # noqa: E402
from ski import pipeline  # noqa: E402
from ski.db import (connect, max_observation_date,  # noqa: E402
                    read_observations, upsert_observations)
from ski.sources import snotel  # noqa: E402


def _temp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _obs(d: date, depth: float) -> dict:
    return {"date": pd.Timestamp(d), "swe_inches": depth / 10.0,
            "snow_depth_inches": depth, "new_snow_24hr": 1.0}


def test_max_observation_date_none_when_empty():
    path = _temp_db()
    try:
        assert max_observation_date(path, "766:UT:SNTL") is None
    finally:
        os.unlink(path)


def test_max_observation_date_returns_latest():
    path = _temp_db()
    try:
        station = "766:UT:SNTL"
        df = pd.DataFrame([_obs(date(2026, 1, 1), 10), _obs(date(2026, 1, 5), 14)])
        upsert_observations(path, station, df)
        assert max_observation_date(path, station) == date(2026, 1, 5)
    finally:
        os.unlink(path)


def test_ingest_empty_station_pulls_full(monkeypatch):
    """No stored rows -> since is None -> the source client gets a full pull."""
    path = _temp_db()
    captured = {}

    def fake_fetch(station, since=None):
        captured["since"] = since
        return pd.DataFrame([_obs(date(2026, 1, 1), 10)])

    monkeypatch.setitem(pipeline.SOURCES["snotel"], "fetch", fake_fetch)
    try:
        pipeline.ingest_mountain("alta", db_path=path)
        assert captured["since"] is None
    finally:
        os.unlink(path)


def test_ingest_warm_station_pulls_only_tail(monkeypatch):
    """With stored history, since = latest - INGEST_OVERLAP_DAYS (the tail)."""
    path = _temp_db()
    station = pipeline.mountain_station(pipeline.get_mountain("alta"))
    upsert_observations(path, station, pd.DataFrame([_obs(date(2026, 3, 20), 40)]))
    captured = {}

    def fake_fetch(st, since=None):
        captured["since"] = since
        return pd.DataFrame([_obs(date(2026, 3, 25), 42)])

    monkeypatch.setitem(pipeline.SOURCES["snotel"], "fetch", fake_fetch)
    try:
        pipeline.ingest_mountain("alta", db_path=path)
        assert captured["since"] == date(2026, 3, 20) - timedelta(days=INGEST_OVERLAP_DAYS)
    finally:
        os.unlink(path)


def test_ingest_full_flag_forces_full_pull(monkeypatch):
    """full=True ignores stored history and pulls the whole record."""
    path = _temp_db()
    station = pipeline.mountain_station(pipeline.get_mountain("alta"))
    upsert_observations(path, station, pd.DataFrame([_obs(date(2026, 3, 20), 40)]))
    captured = {}

    def fake_fetch(st, since=None):
        captured["since"] = since
        return pd.DataFrame([_obs(date(2026, 3, 25), 42)])

    monkeypatch.setitem(pipeline.SOURCES["snotel"], "fetch", fake_fetch)
    try:
        pipeline.ingest_mountain("alta", db_path=path, full=True)
        assert captured["since"] is None
    finally:
        os.unlink(path)


def test_mean_temp_roundtrips_and_is_optional():
    """A frame WITH mean_temp_f stores and reads back; a frame WITHOUT it (SWE
    sources) still writes, and that column reads back NULL/NaN."""
    path = _temp_db()
    try:
        station = "USC00000001"
        with_temp = pd.DataFrame([
            {**_obs(date(2026, 1, 1), 10), "mean_temp_f": 18.0},
            {**_obs(date(2026, 1, 2), 12), "mean_temp_f": np.nan},
        ])
        upsert_observations(path, station, with_temp)
        df = read_observations(path, station)
        assert "mean_temp_f" in df.columns
        assert df["mean_temp_f"].iloc[0] == 18.0
        assert pd.isna(df["mean_temp_f"].iloc[1])
        # A SWE-source frame (no temp column) must still upsert fine.
        upsert_observations(path, "766:UT:SNTL", pd.DataFrame([_obs(date(2026, 1, 1), 10)]))
        assert read_observations(path, "766:UT:SNTL")["mean_temp_f"].isna().all()
    finally:
        os.unlink(path)


def test_incremental_tail_without_temp_preserves_backfilled_temp():
    """COALESCE guard: a later incremental row lacking mean_temp_f must not null out
    a temperature an earlier full pull already stored for that (station, date)."""
    path = _temp_db()
    try:
        station = "USC00000002"
        upsert_observations(path, station, pd.DataFrame([
            {**_obs(date(2026, 1, 1), 10), "mean_temp_f": 20.0}]))
        # Re-ingest the same day from a frame with NO temp column (e.g. a source that
        # dropped it) -> the stored 20.0 must survive.
        upsert_observations(path, station, pd.DataFrame([_obs(date(2026, 1, 1), 11)]))
        df = read_observations(path, station)
        assert df["mean_temp_f"].iloc[0] == 20.0
        assert df["snow_depth_inches"].iloc[0] == 11.0    # the rest still updates
    finally:
        os.unlink(path)


def test_migration_adds_temp_column_to_legacy_db():
    """A DB created under the pre-temp schema gains mean_temp_f on next connect()."""
    path = _temp_db()
    try:
        # Build a legacy table WITHOUT mean_temp_f, then let connect() migrate it.
        raw = sqlite3.connect(path)
        raw.execute("CREATE TABLE raw_observations (station_id TEXT, date TEXT, "
                    "swe_inches REAL, snow_depth_inches REAL, new_snow_24hr REAL, "
                    "PRIMARY KEY (station_id, date))")
        raw.execute("INSERT INTO raw_observations VALUES ('S', '2026-01-01', 1, 2, 0.5)")
        raw.commit()
        raw.close()
        conn = connect(path)                    # triggers _migrate
        cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_observations)")}
        conn.close()
        assert "mean_temp_f" in cols
        # Legacy row survived and reads back with NaN temp.
        df = read_observations(path, "S")
        assert len(df) == 1 and pd.isna(df["mean_temp_f"].iloc[0])
    finally:
        os.unlink(path)


def test_snotel_since_pins_a_concrete_end_not_por_end(monkeypatch):
    """Regression: the SNOTEL report endpoint returns an error PAGE (not CSV)
    for an ISO start paired with POR_END, so an incremental fetch must swap in a
    concrete end date. Assert the requested URL carries no POR_END."""
    seen = {}

    class _Resp:
        text = ("#header\n"
                "Date,Foo Snow Water Equivalent (in),Foo Snow Depth (in)\n"
                "2026-07-15,1.0,2.0\n")
        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(snotel.http, "get", fake_get)
    snotel.fetch_station_daily("766:UT:SNTL", since=date(2026, 7, 3))
    assert "POR_END" not in seen["url"]
    assert "2026-07-03" in seen["url"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
