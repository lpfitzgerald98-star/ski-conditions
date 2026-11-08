"""Tests for scripts/push_alerts.py: which snow alerts go out, and how often."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from push_alerts import MAX_PER_DAY, find_events, message, pick  # noqa: E402

TODAY = date(2026, 12, 10)


def row(key="alta", **kw):
    base = {"key": key, "name": f"{key.title()}, UT", "in_season": True, "season_over_since": None,
            "preseason_grade": None, "grade": "B", "score": 60.0, "alert": False,
            "fresh_24h": 0.0, "fresh_7d": 4.0, "base_depth": 40.0, "incoming_inches": 0.0}
    base.update(kw)
    return base


def kinds(events):
    return sorted((e.kind, e.key) for e in events)


def test_a_storm_alert_becomes_a_storm_event():
    cards = {"alta": {"forecast_horizons": [{"horizon_hours": 24, "inches": 8, "percentile": 92},
                                            {"horizon_hours": 72, "inches": 22, "percentile": 97}]}}
    [e] = find_events([row(alert=True)], None, cards)
    assert e.kind == "storm"
    assert '22" forecast in the next 72 hours' in e.body and "top 3%" in e.body


def test_powder_needs_the_mountains_own_24h_floor():
    # Alta's floor is 8"; 7" isn't a powder alert, 10" is.
    assert find_events([row(fresh_24h=7.0)], None, {}) == []
    [e] = find_events([row(fresh_24h=10.0)], None, {})
    assert e.kind == "powder" and e.strength == 10 / 8


def test_powder_on_a_closed_or_not_open_mountain_is_ignored():
    assert find_events([row(fresh_24h=12.0, preseason_grade="B")], None, {}) == []
    assert find_events([row(fresh_24h=12.0, in_season=False)], None, {}) == []
    assert find_events([row(fresh_24h=12.0, season_over_since="Apr 26")], None, {}) == []


def test_opening_and_reaching_an_a_need_the_previous_build():
    now = [row(grade="A-")]
    assert find_events(now, None, {}) == []
    before = [row(grade="B+", preseason_grade="B")]
    assert kinds(find_events(now, before, {})) == [("grade_a", "alta"), ("opens", "alta")]


def test_staying_at_an_a_is_not_news():
    assert find_events([row(grade="A")], [row(grade="A-")], {}) == []


def test_the_best_event_leads_and_names_the_next_ones():
    events = find_events([row("alta", fresh_24h=16.0), row("snowbird", fresh_24h=9.0),
                          row("brighton", fresh_24h=12.0)], None, {})
    ranked = pick(events, [], TODAY)
    assert [e.key for e in ranked] == ["alta", "brighton", "snowbird"]
    msg = message(ranked)
    assert msg["title"] == "Powder day: Alta, UT"
    assert "Also worth a look: Brighton, Snowbird." in msg["body"]
    assert msg["url"].endswith("?m=alta")


def test_the_same_event_isnt_resent_within_its_cooldown():
    events = find_events([row(fresh_24h=10.0)], None, {})
    assert pick(events, [{"date": "2026-12-10", "kind": "powder", "key": "alta"}], TODAY) == []
    assert pick(events, [{"date": "2026-12-09", "kind": "powder", "key": "alta"}], TODAY) != []
    storm = find_events([row(alert=True)], None, {})
    assert pick(storm, [{"date": "2026-12-08", "kind": "storm", "key": "alta"}], TODAY) == []
    assert pick(storm, [{"date": "2026-12-07", "kind": "storm", "key": "alta"}], TODAY) != []


def test_no_more_than_the_daily_limit_go_out():
    events = find_events([row(fresh_24h=20.0)], None, {})
    log = [{"date": "2026-12-10", "kind": "storm", "key": f"m{i}"} for i in range(MAX_PER_DAY)]
    assert pick(events, log, TODAY) == []
