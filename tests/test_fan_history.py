"""The season so far, drawn to the left of the fan.

The one property that matters: the solid line must end exactly where every
simulated season begins. If it ended a point higher or lower, the chart
would show a step at "today" that never happened.
"""
import json
import re

import build_report
from conftest import LEAGUE
from test_projection_trend import CLUBS, _fixtures


def _history(db):
    r = build_report._compute_projection(db, LEAGUE)
    start, hist = build_report._fan_history(db, LEAGUE, r["teams"])
    return r, start, hist


def test_every_history_ends_where_its_fan_begins(db):
    _fixtures(db)
    r, _start, hist = _history(db)
    for i in range(r["n"]):
        assert hist[i][-1][1] == r["base_pts"][i], r["teams"][i]


def test_every_club_starts_from_nothing_on_the_opening_day(db):
    _fixtures(db)
    r, start, hist = _history(db)
    assert start == "2026-04-01"
    assert all(h[0] == [0, 0] for h in hist)


def test_each_step_is_one_result(db):
    """A win, a draw or a defeat: three points, one or none."""
    _fixtures(db)
    _r, _start, hist = _history(db)
    for h in hist:
        steps = [b[1] - a[1] for a, b in zip(h, h[1:])]
        days = [b[0] - a[0] for a, b in zip(h, h[1:])]
        assert all(s in (0, 1, 3) for s in steps)
        assert all(d >= 0 for d in days), "never runs backwards in time"


def test_before_a_ball_is_kicked_there_is_no_past(db):
    _fixtures(db, played_rounds=0)
    clubs = sorted(CLUBS)
    assert build_report._fan_history(db, LEAGUE, clubs) == (None, [])


def test_the_fan_carries_it(db):
    _fixtures(db)
    html = build_report.season_projection_fan(db, LEAGUE)
    raw = re.search(r"class='fan-data'>(.*?)</script>", html, re.S).group(1)
    fan = json.loads(raw.replace("<\/", "</"))
    assert fan["start"] == "2026-04-01" and len(fan["hist"]) == 6


def test_draws_and_away_wins_count_too(db):
    """The shared fixture only has home wins; a real season does not."""
    _fixtures(db)
    db.execute("UPDATE matches SET home_score = 1, away_score = 1 WHERE event_id = '1'")
    db.execute("UPDATE matches SET home_score = 0, away_score = 2 WHERE event_id = '2'")
    db.commit()
    r, _start, hist = _history(db)
    for i in range(r["n"]):
        assert hist[i][-1][1] == r["base_pts"][i], r["teams"][i]
    steps = {b[1] - a[1] for h in hist for a, b in zip(h, h[1:])}
    assert {0, 1, 3} <= steps, "a win, a draw and a defeat all appear"
