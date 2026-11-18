"""Tests for scripts/push_alerts.py: which snow alerts go out, and how often."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from push_alerts import (  # noqa: E402
    DEFAULT_PREFS, MAX_PER_DAY, choose, find_events, message, pick, prefs_of, rank, trim_history,
)

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
    # A tap opens the Top picks panel (not one mountain), with the lead one marked.
    assert msg["url"].endswith("?top=recent&hl=alta")


def test_the_same_event_isnt_resent_within_its_cooldown():
    events = find_events([row(fresh_24h=10.0)], None, {})
    assert pick(events, [{"date": "2026-12-10", "kind": "powder", "key": "alta"}], TODAY) == []
    assert pick(events, [{"date": "2026-12-09", "kind": "powder", "key": "alta"}], TODAY) != []
    storm = find_events([row(alert=True)], None,
                        {"alta": {"forecast_horizons": [{"horizon_hours": 24, "inches": 10, "percentile": 95}]}})
    assert storm
    assert pick(storm, [{"date": "2026-12-08", "kind": "storm", "key": "alta"}], TODAY) == []
    assert pick(storm, [{"date": "2026-12-07", "kind": "storm", "key": "alta"}], TODAY) != []


def test_no_more_than_the_daily_limit_go_out():
    events = find_events([row(fresh_24h=20.0)], None, {})
    log = [{"date": "2026-12-10", "kind": "storm", "key": f"m{i}"} for i in range(MAX_PER_DAY)]
    assert pick(events, log, TODAY) == []


# -- per-phone choice ----------------------------------------------------------

def prefs(**kw):
    return {**DEFAULT_PREFS, **kw}


def three_powder_days():
    # alta strongest, then brighton, then snowbird; mammoth in another region.
    rows = [row("alta", fresh_24h=16.0, region="Utah"), row("brighton", fresh_24h=12.0, region="Utah"),
            row("snowbird", fresh_24h=9.0, region="Utah"),
            row("mammoth", name="Mammoth, CA", fresh_24h=13.0, region="Tahoe & Sierra")]
    return rank(find_events(rows, None, {}))


def test_old_subscriptions_without_settings_get_the_best_anywhere():
    p = prefs_of({"endpoint": "x"})
    assert p == DEFAULT_PREFS
    c = choose(three_powder_days(), p, [], TODAY)
    assert c.why == "best" and c.lead.key == "alta"
    assert [e.key for e in c.others] == ["mammoth", "brighton"]


def test_a_followed_mountain_beats_a_bigger_storm_elsewhere():
    c = choose(three_powder_days(), prefs(mountains=["snowbird"]), [], TODAY)
    assert c.why == "follow" and c.lead.key == "snowbird"
    assert [e.key for e in c.others] == ["alta", "mammoth"]   # then the best of the rest


def test_following_a_region_covers_its_mountains_and_parent_regions_work():
    c = choose(three_powder_days(), prefs(regions=["Tahoe & Sierra"]), [], TODAY)
    assert c.why == "follow" and c.lead.key == "mammoth"
    # A parent region covers every leaf under it: Western North America has both.
    c = choose(three_powder_days(), prefs(regions=["Western North America"], best=False), [], TODAY)
    assert c.why == "follow" and c.lead.key == "alta"
    assert choose(three_powder_days(), prefs(regions=["Japan"], best=False), [], TODAY) is None


def test_with_nothing_followed_happening_the_best_anywhere_still_goes_out():
    c = choose(three_powder_days(), prefs(mountains=["jackson"]), [], TODAY)
    assert c.why == "best" and c.lead.key == "alta"


def test_best_anywhere_off_means_only_your_mountains():
    assert choose(three_powder_days(), prefs(best=False, mountains=["jackson"]), [], TODAY) is None
    assert choose(three_powder_days(), prefs(best=False, mountains=["alta"]), [], TODAY).lead.key == "alta"


def test_kinds_turned_off_never_go_out_even_for_followed_mountains():
    assert choose(three_powder_days(), prefs(kinds=["storm"], mountains=["alta"]), [], TODAY) is None


def test_cooldowns_are_per_phone():
    seen = [{"date": "2026-12-10", "kind": "powder", "key": "alta", "why": "best"}]
    c = choose(three_powder_days(), prefs(), seen, TODAY)
    assert c.lead.key == "mammoth"                       # alta already sent to this phone
    assert choose(three_powder_days(), prefs(), [], TODAY).lead.key == "alta"   # a fresh phone still gets it


def test_daily_limit_is_per_phone():
    full = [{"date": "2026-12-10", "kind": "storm", "key": f"m{i}", "why": "follow"} for i in range(MAX_PER_DAY)]
    assert choose(three_powder_days(), prefs(mountains=["alta"]), full, TODAY) is None
    yesterday = [{**h, "date": "2026-12-09"} for h in full]
    assert choose(three_powder_days(), prefs(mountains=["alta"]), yesterday, TODAY) is not None


def test_best_anywhere_leaves_room_for_followed_mountains():
    had_best = [{"date": "2026-12-10", "kind": "storm", "key": "x", "why": "best"}]
    # Following something: one "best anywhere" a day, so their mountains keep a slot.
    assert choose(three_powder_days(), prefs(mountains=["jackson"]), had_best, TODAY) is None
    assert choose(three_powder_days(), prefs(mountains=["alta"]), had_best, TODAY).why == "follow"
    # Following nothing: best anywhere can use both slots, as before.
    assert choose(three_powder_days(), prefs(), had_best, TODAY).why == "best"


def test_notes_and_history_trim():
    c = choose(three_powder_days(), prefs(mountains=["jackson"]), [], TODAY)
    assert message([c.lead], "Best snow anywhere today.")["body"].endswith("Best snow anywhere today.")
    old = [{"date": "2026-01-01", "kind": "opens", "key": "a", "why": "best"},
           {"date": "2026-12-01", "kind": "opens", "key": "b", "why": "best"}]
    assert [h["key"] for h in trim_history(old, TODAY)] == ["b"]


def test_send_gives_each_phone_its_own_alert_and_records_it(monkeypatch):
    import types
    from push_alerts import send

    subs = [{"id": "a" * 32, "endpoint": "https://fcm.googleapis.com/1", "keys": {}, "history": []},
            {"id": "b" * 32, "endpoint": "https://fcm.googleapis.com/2", "keys": {}, "history": [],
             "prefs": {"mountains": ["snowbird"]}},
            {"id": "c" * 32, "endpoint": "https://fcm.googleapis.com/3", "keys": {}, "history": [],
             "prefs": {"best": False, "mountains": ["jackson"]}},
            {"id": "d" * 32, "endpoint": "https://fcm.googleapis.com/gone", "keys": {}, "history": []}]
    pushed, puts, deletes = {}, {}, []

    class WebPushException(Exception):
        def __init__(self, status):
            super().__init__(status)
            self.response = types.SimpleNamespace(status_code=status)

    def webpush(sub, data, **kw):
        if sub["endpoint"].endswith("gone"):
            raise WebPushException(410)
        pushed[sub["endpoint"]] = json.loads(data)

    class Resp:
        def __init__(self, body=None):
            self.body, self.ok, self.status_code = body, True, 200
        def json(self):
            return self.body
        def raise_for_status(self):
            pass

    fake_requests = types.SimpleNamespace(
        get=lambda url, **kw: Resp(subs),
        put=lambda url, json=None, **kw: puts.__setitem__(url.split("/")[-2], json["history"]) or Resp(),
        delete=lambda url, **kw: deletes.append(url.split("/")[-1]) or Resp())
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setitem(sys.modules, "pywebpush", types.SimpleNamespace(webpush=webpush, WebPushException=WebPushException))

    rows = send(three_powder_days(), "https://w.dev", "t", "k", TODAY)
    assert pushed["https://fcm.googleapis.com/1"]["title"] == "Powder day: Alta, UT"
    assert pushed["https://fcm.googleapis.com/2"]["title"] == "Powder day: Snowbird, UT"
    assert "https://fcm.googleapis.com/3" not in pushed          # nothing it follows, best anywhere off
    assert deletes == ["d" * 32]
    assert puts["b" * 32] == [{"date": "2026-12-10", "kind": "powder", "key": "snowbird", "why": "follow"}]
    assert sorted((r["key"], r["sent_to"]) for r in rows) == [("alta", 1), ("snowbird", 1)]


def test_an_old_worker_without_history_falls_back_to_the_shared_log(monkeypatch, tmp_path):
    import types
    import push_alerts

    log = tmp_path / "sent.csv"
    push_alerts.append_log([{"date": "2026-12-10", "kind": "powder", "key": "alta", "sent_to": 1}], log)
    monkeypatch.setattr(push_alerts, "LOG", log)
    monkeypatch.setattr(push_alerts.read_log, "__defaults__", (log,))
    pushed, puts = [], []

    class Resp:
        ok, status_code = True, 200
        def json(self):
            return [{"id": "a" * 32, "endpoint": "https://fcm.googleapis.com/1", "keys": {}}]
        def raise_for_status(self):
            pass

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(
        get=lambda *a, **k: Resp(), put=lambda *a, **k: puts.append(a) or Resp(), delete=lambda *a, **k: Resp()))
    monkeypatch.setitem(sys.modules, "pywebpush", types.SimpleNamespace(
        webpush=lambda sub, data, **kw: pushed.append(json.loads(data)), WebPushException=Exception))
    push_alerts.send(three_powder_days(), "https://w.dev", "t", "k", TODAY)
    assert pushed[0]["title"] == "Powder day: Mammoth, CA"   # alta already went out today
    assert puts == []


def test_a_storm_flag_without_snow_the_card_counts_sends_nothing():
    # 2026-11-17 Snowbird: the provider said 18" but the high was 41F, so the
    # card's temperature-checked forecast was 0" at every horizon. No alert, and
    # never the raw 18" or a made-up "top 10%".
    cards = {"snowbird": {"forecast_horizons": [
        {"horizon_hours": h, "inches": 0.0, "percentile": 0.0, "tmax_f": 41} for h in (24, 48, 72)]}}
    assert find_events([row("snowbird", alert=True, incoming_inches=18.0)], None, cards) == []
    assert find_events([row("snowbird", alert=True, incoming_inches=18.0)], None, {}) == []


def test_a_storm_needs_the_mountains_floor_and_top_decile():
    def ev(inches, pct, hours=24):
        cards = {"alta": {"forecast_horizons": [{"horizon_hours": hours, "inches": inches, "percentile": pct}]}}
        return find_events([row(alert=True)], None, cards)
    assert ev(7, 95) == []                 # under Alta's 8" 24h floor
    assert ev(9, 85) == []                 # not top 10%
    assert ev(9, None) == []               # no ranking, no claim
    assert ev(14, 95, hours=72) == []      # under the 15" 72h floor
    [e] = ev(9, 95)
    assert '9" forecast in the next 24 hours, top 5%' in e.body


def test_a_storm_alert_opens_the_forecast_tab_for_its_window():
    cards = {"alta": {"forecast_horizons": [{"horizon_hours": 48, "inches": 14, "percentile": 96}]}}
    [e] = find_events([row(alert=True)], None, cards)
    assert message([e])["url"].endswith("?top=48&hl=alta")
