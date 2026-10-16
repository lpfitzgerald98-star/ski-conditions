"""Seasonal forecast weight for the live board (config.FORECAST_WEIGHT_SCHEDULE):
  - comparable.with_forecast_weight  -- terrain fixed, total preserved
  - comparable.scheduled_weights     -- early/late switch on season_progress
  - live_row_weights / attach_*      -- off by default; when on, per-mountain
  - hemisphere: keyed on each mountain's own season_window, not the calendar
"""

from __future__ import annotations

from datetime import date

import pytest

from config import FORECAST_WEIGHT_SCHEDULE, GLOBAL_SCORE_WEIGHTS, MOUNTAINS
from ski import comparable
from ski.watercalendar import season_progress

ON = {**FORECAST_WEIGHT_SCHEDULE, "enabled": True}
TERRAIN = comparable.TERRAIN_COMPONENTS


def test_with_forecast_weight_holds_terrain_and_total():
    for w in (0.0, 0.08, 0.12, 0.1613, 0.24):
        out = comparable.with_forecast_weight(GLOBAL_SCORE_WEIGHTS, w)
        assert out["forecast"] == w
        assert sum(out.values()) == pytest.approx(sum(GLOBAL_SCORE_WEIGHTS.values()))
        for k in TERRAIN:
            assert out[k] == GLOBAL_SCORE_WEIGHTS[k]
    # ratios among the other snow components are preserved
    out = comparable.with_forecast_weight(GLOBAL_SCORE_WEIGHTS, 0.08)
    assert out["base"] / out["fresh"] == pytest.approx(
        GLOBAL_SCORE_WEIGHTS["base"] / GLOBAL_SCORE_WEIGHTS["fresh"])


def test_current_weight_is_a_fixed_point():
    out = comparable.with_forecast_weight(GLOBAL_SCORE_WEIGHTS, GLOBAL_SCORE_WEIGHTS["forecast"])
    for k, v in GLOBAL_SCORE_WEIGHTS.items():
        assert out[k] == pytest.approx(v)


@pytest.mark.parametrize("progress, expected", [
    (None, "early_weight"), (0.0, "early_weight"), (0.24, "early_weight"),
    (0.25, "late_weight"), (0.6, "late_weight"), (1.0, "late_weight"),
])
def test_scheduled_weights_switch(progress, expected):
    out = comparable.scheduled_weights(progress, GLOBAL_SCORE_WEIGHTS, ON)
    assert out["forecast"] == ON[expected]


def test_schedule_is_off_by_default_and_attach_matches_flat():
    assert FORECAST_WEIGHT_SCHEDULE["enabled"] is False
    assert comparable.live_row_weights(GLOBAL_SCORE_WEIGHTS) is None
    assert FORECAST_WEIGHT_SCHEDULE["early_weight"] == GLOBAL_SCORE_WEIGHTS["forecast"]


def _row(key, forecast, progress, base=50.0):
    return {"key": key, "region": "R", "in_season": True, "season_progress": progress,
            "abs_base_in": base, "abs_fresh_in": 5.0, "abs_season_in": 100.0,
            "abs_forecast_in": forecast, "abs_quality": 60.0, "abs_vertical_ft": 2000,
            "abs_acres": 1000, "abs_pct_advanced_expert": 30}


def test_when_enabled_late_season_mountain_leans_less_on_forecast(monkeypatch):
    # Two mountains with the same profile: "storm" has the most incoming snow but
    # the thinnest base. Late in its season the forecast counts less, so its
    # score drops relative to flat weights; early in its season it doesn't.
    rows = [_row("storm", 20.0, None, base=10.0)] + \
        [_row(f"m{i}", float(i), None, base=40.0 + i) for i in range(6)]
    flat = comparable.score_population(rows, GLOBAL_SCORE_WEIGHTS)
    for r in rows:
        r["season_progress"] = 0.6
    late = comparable.score_population(
        rows, GLOBAL_SCORE_WEIGHTS,
        row_weights=lambda r: comparable.scheduled_weights(r["season_progress"],
                                                           GLOBAL_SCORE_WEIGHTS, ON))
    assert late["storm"] < flat["storm"]
    for r in rows:
        r["season_progress"] = 0.1
    early = comparable.score_population(
        rows, GLOBAL_SCORE_WEIGHTS,
        row_weights=lambda r: comparable.scheduled_weights(r["season_progress"],
                                                           GLOBAL_SCORE_WEIGHTS, ON))
    assert early["storm"] == pytest.approx(flat["storm"])

    # attach_* pick the schedule up from config when it's enabled
    monkeypatch.setattr(comparable, "FORECAST_WEIGHT_SCHEDULE", ON)
    for r in rows:
        r["season_progress"] = 0.6
    monkeypatch.setattr(comparable.live_row_weights, "__defaults__", (ON,))
    comparable.attach_global_score(rows)
    assert next(r for r in rows if r["key"] == "storm")["global_score"] == \
        pytest.approx(round(late["storm"], 1))


def test_hemisphere_correct_switch_dates():
    # A typical NH mountain is late-season by mid-January; a NZ one is still early
    # then (its season hasn't started) and goes late by August.
    nh = MOUNTAINS["alta"]["season_window"]
    nz = MOUNTAINS["mt_hutt"]["season_window"]
    thr = ON["late_from_progress"]
    assert season_progress(date(2027, 1, 20), nh["start"], nh["end"]) >= thr
    assert season_progress(date(2026, 12, 1), nh["start"], nh["end"]) < thr
    assert season_progress(date(2027, 1, 20), nz["start"], nz["end"]) < thr or \
        season_progress(date(2027, 1, 20), nz["start"], nz["end"]) == 1.0
    assert season_progress(date(2027, 6, 20), nz["start"], nz["end"]) < thr
    assert season_progress(date(2027, 8, 15), nz["start"], nz["end"]) >= thr
