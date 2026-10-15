"""Live-only signal logging for the mid-season backtest:
  - ski.quality_log           -- record / read, first-of-day wins, NULL handling
  - pipeline wiring           -- a live scorecard writes one quality_log row
  - scripts/sync_logs.py      -- DB <-> committed CSV mirror (round trip, never shrinks)
  - scripts/backtest_forecast -- verdict thresholds + empty-data report

No network: the outlook is a synthetic object, the DB a temp SQLite file.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from config import MOUNTAINS  # noqa: E402
from ski import forecast_log, pipeline, quality_log  # noqa: E402
from ski.db import upsert_observations  # noqa: E402
from ski.sources.outlook import CurrentWeather, Outlook, Recent  # noqa: E402

import backtest_forecast  # noqa: E402
import sync_logs  # noqa: E402


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


# --- quality_log -------------------------------------------------------------
def test_quality_log_round_trip_and_first_of_day_wins():
    db = _tmp_db()
    try:
        assert quality_log.record(db, "alta", date(2026, 12, 1), "nws",
                                  {"wind_quality": 40.0, "thaw_index": 0.2,
                                   "not_a_field": 9.0, "weather_quality": float("nan")})
        quality_log.record(db, "alta", date(2026, 12, 1), "nws", {"wind_quality": 99.0})
        df = quality_log.read_log(db, mountain_key="alta")
        assert len(df) == 1
        row = df.iloc[0]
        assert row["wind_quality"] == 40.0          # first write kept
        assert row["thaw_index"] == 0.2
        assert pd.isna(row["weather_quality"])      # NaN stored as NULL
        assert row["provider"] == "nws"
    finally:
        os.remove(db)


def _seed(db, station, days=60):
    dates = pd.date_range(end=pd.Timestamp("2026-12-01"), periods=days, freq="D")
    upsert_observations(db, station, pd.DataFrame({
        "date": dates,
        "swe_inches": np.nan,
        "snow_depth_inches": np.linspace(10, 60, days),
        "new_snow_24hr": np.where(np.arange(days) % 5 == 0, 6.0, 0.0),
        "mean_temp_f": 20.0,
    }))


def test_live_scorecard_writes_a_quality_log_row(monkeypatch):
    db = _tmp_db()
    try:
        key = "alta"
        _seed(db, pipeline.mountain_station(MOUNTAINS[key]))
        outlook = Outlook(
            provider="openmeteo", snow_in={24: 3.0, 48: 5.0, 72: 8.0},
            rain_72h_in=0.0, tmax_72h_f=25.0, tmax_by_window={24: 25.0, 48: 25.0, 72: 25.0},
            current=CurrentWeather(temperature_f=18.0, wind_mph=10.0, sky_cover_pct=50.0),
            recent=Recent(rain_72h_in=0.0, tmax_72h_f=28.0, tmin_24h_f=10.0,
                          snowfall_72h_in=6.0, snow_temp_72h_f=20.0,
                          wind_sustained_72h_mph=25.0))
        monkeypatch.setattr(pipeline, "fetch_outlook_for_mountain", lambda k: outlook)
        pipeline.mountain_scorecard(key, db_path=db, as_of=date(2026, 12, 1))
        df = quality_log.read_log(db, mountain_key=key)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["wind_sustained_72h_mph"] == 25.0
        assert row["current_temp_f"] == 18.0
        assert row["recent_snowfall_72h_in"] == 6.0
        assert row["wind_quality"] is not None and not pd.isna(row["wind_quality"])
    finally:
        os.remove(db)


def test_offline_scorecard_logs_nothing():
    db = _tmp_db()
    try:
        _seed(db, pipeline.mountain_station(MOUNTAINS["alta"]))
        pipeline.mountain_scorecard("alta", db_path=db, as_of=date(2026, 12, 1),
                                    use_network=False)
        assert quality_log.read_log(db).empty
    finally:
        os.remove(db)


# --- sync_logs ---------------------------------------------------------------
def test_sync_logs_round_trip_is_stable_and_never_shrinks(tmp_path):
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    logs = tmp_path / "logs"
    forecast_log.record(src, "alta", date(2026, 11, 30), 24, 1.0 / 3.0, 55.5, 20.0)
    forecast_log.record(src, "alta", date(2026, 12, 1), 72, 8.0, 70.0, 22.0)
    quality_log.record(src, "alta", date(2026, 12, 1), "nws", {"wind_quality": 41.0})
    sync_logs.export(src, logs)
    assert sorted(p.name for p in (logs / "forecast_log").glob("*.csv")) == \
        ["2026-11.csv", "2026-12.csv"]
    snap = {p: p.read_bytes() for p in logs.rglob("*.csv")}

    # A cold DB gets the history back, idempotently...
    assert sync_logs.import_(dst, logs) == {"forecast_log": 2, "quality_log": 1}
    assert sync_logs.import_(dst, logs) == {"forecast_log": 0, "quality_log": 0}
    # ...and re-exporting it changes no bytes.
    sync_logs.export(dst, logs)
    assert {p: p.read_bytes() for p in logs.rglob("*.csv")} == snap

    # An export from a DB that lost history keeps the committed rows.
    empty_ish = tmp_path / "lost.db"
    forecast_log.record(empty_ish, "alta", date(2026, 12, 2), 24, 0.0, 0.0, 30.0)
    sync_logs.export(empty_ish, logs)
    dec = pd.read_csv(logs / "forecast_log" / "2026-12.csv")
    assert set(dec["as_of"]) == {"2026-12-01", "2026-12-02"}


# --- backtest_forecast -------------------------------------------------------
def test_verdict_thresholds():
    v = backtest_forecast._verdict
    assert v(np.full(12, 0.05)).startswith("HELPS")
    assert v(np.full(12, -0.05)).startswith("HURTS")
    assert v(np.full(12, 0.005)).startswith("inconclusive")
    assert v(np.full(3, 0.5)).startswith("not enough data")


def test_default_since_is_nh_season_start():
    assert backtest_forecast.default_since(date(2026, 12, 15)) == date(2026, 11, 1)
    assert backtest_forecast.default_since(date(2027, 2, 15)) == date(2026, 11, 1)


def test_report_renders_with_no_data():
    md = backtest_forecast.to_markdown(
        {}, {"n_dates": 0, "summary": {}, "per_date": {}, "logged_dates": 0},
        date(2026, 11, 1), date(2026, 12, 15))
    assert "No logged forecasts" in md and "Not enough eligible dates" in md
