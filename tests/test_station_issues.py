"""Tests for scripts/station_issues.py: station problems become GitHub issues."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from station_issues import BACK_TITLE, STATIONS_TITLE, stations_body, sync  # noqa: E402


class FakeGh:
    """Stands in for the gh CLI: holds open issues, records every write."""

    def __init__(self, issues=None):
        self.issues = issues or []
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.issues)
        return ""

    def writes(self):
        return [c[:2] for c in self.calls if c[1] != "list"]


def _report(*keys, back=()):
    return {"as_of": "2026-11-08",
            "flagged": [{"key": k, "name": k.title(), "station": "S", "source": "eccc 1",
                         "reason": "silent 30 days in season"} for k in keys],
            "back": [{"key": b, "name": b.title(), "old_station": "Old", "old_id": "eccc 2",
                      "latest": "2026-11-07", "current_station": "New"} for b in back]}


def _open(report):
    return [{"number": 7, "title": STATIONS_TITLE, "body": stations_body(report)}]


def test_the_first_silent_station_opens_an_issue():
    gh = FakeGh()
    sync(_report("nakiska"), gh)
    assert gh.writes() == [["issue", "create"]]
    assert "Nakiska" in gh.calls[-1][-1]


def test_the_same_stations_refresh_the_issue_without_a_notification():
    gh = FakeGh(_open(_report("nakiska")))
    sync(_report("nakiska"), gh)
    assert gh.writes() == [["issue", "edit"]]


def test_a_change_in_which_stations_are_silent_adds_a_comment():
    gh = FakeGh(_open(_report("nakiska")))
    done = sync(_report("nakiska", "whistler"), gh)
    assert gh.writes() == [["issue", "edit"], ["issue", "comment"]]
    assert "newly silent: whistler" in done[0]


def test_the_issue_closes_once_every_station_reports():
    gh = FakeGh(_open(_report("nakiska")))
    sync(_report(), gh)
    assert gh.writes() == [["issue", "close"]]


def test_nothing_happens_when_all_is_well():
    gh = FakeGh()
    assert sync(_report(), gh) == ["nothing to do"]
    assert gh.writes() == []


def test_a_returning_station_opens_its_own_issue_once():
    gh = FakeGh()
    sync(_report(back=["fernie"]), gh)
    assert gh.writes() == [["issue", "create"]]
    assert BACK_TITLE in gh.calls[-1]
    gh = FakeGh([{"number": 9, "title": BACK_TITLE, "body": ""}])
    sync(_report(back=["fernie"]), gh)
    assert gh.writes() == []


def test_an_issue_with_a_similar_title_is_not_mistaken_for_ours():
    gh = FakeGh([{"number": 3, "title": STATIONS_TITLE + " (old)", "body": ""}])
    sync(_report("nakiska"), gh)
    assert gh.writes() == [["issue", "create"]]
