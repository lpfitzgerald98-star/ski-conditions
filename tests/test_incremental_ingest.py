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

from config import INGEST_OVERLAP_DAYS  # noqa: E402
from ski import pipeline  # noqa: E402
from ski.db import max_observation_date, upsert_observations  # noqa: E402
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
