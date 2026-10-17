"""When the score changes: game states from the shot maps.

Each test builds a match whose answer can be worked out by hand.
"""
import pytest

import build_report
from conftest import LEAGUE

SEASON = "2026"


def _match(db, mid, home="H", away="A", hs=0, as_=0):
    for team, opp, side, gf, ga in ((home, away, "h", hs, as_), (away, home, "a", as_, hs)):
        db.execute(
            "INSERT INTO fotmob_team_matches (season, league, match_id, team, opponent, "
            "match_date, home_away, scored, missed, pts) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (SEASON, LEAGUE, mid, team, opp, "2026-05-01", side, gf, ga,
             3 if gf > ga else 1 if gf == ga else 0))


def _shot(db, mid, sid, team, minute, xg=0.1, goal=False, period=None, added=0,
          situation="RegularPlay", own_goal=0):
    period = period or ("FirstHalf" if minute <= 45 else "SecondHalf")
    db.execute(
        "INSERT INTO fotmob_match_shots (season, league, match_id, shot_id, team, minute, "
        "minute_added, period, xg, outcome, situation, is_own_goal) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (SEASON, LEAGUE, mid, sid, team, minute, added, period,
         None if own_goal else xg, "Goal" if goal else "Miss", situation, own_goal))


def test_minutes_add_up_to_the_match(db):
    """No stoppage shots, so the match is exactly 90 minutes per side."""
    _match(db, "m1", hs=1)
    _shot(db, "m1", "s1", "H", 30, goal=True)
    st = build_report._score_states(db, LEAGUE)
    assert sum(st["H"]["min"]) == pytest.approx(90)
    assert st["H"]["min"] == pytest.approx([60, 30, 0])
    assert st["A"]["min"] == pytest.approx([0, 30, 60])


def test_stoppage_lengthens_the_match_by_what_the_shots_show(db):
    _match(db, "m1")
    _shot(db, "m1", "s1", "H", 45, period="FirstHalf", added=3)
    _shot(db, "m1", "s2", "A", 90, period="SecondHalf", added=5)
    st = build_report._score_states(db, LEAGUE)
    assert sum(st["H"]["min"]) == pytest.approx(98)


def test_a_shot_counts_in_the_state_before_its_own_goal(db):
    _match(db, "m1", hs=1)
    _shot(db, "m1", "g", "H", 20, xg=0.5, goal=True)      # level when struck
    _shot(db, "m1", "after", "H", 60, xg=0.2)              # ahead by then
    _shot(db, "m1", "reply", "A", 70, xg=0.3)              # behind by then
    st = build_report._score_states(db, LEAGUE)
    assert st["H"]["for"] == pytest.approx([0.2, 0.5, 0])
    assert st["A"]["for"] == pytest.approx([0, 0, 0.3])
    assert st["H"]["against"] == pytest.approx([0.3, 0, 0])


def test_an_own_goal_counts_for_the_other_side(db):
    """The shot map files an own goal under the side that conceded it."""
    _match(db, "m1", as_=1)
    _shot(db, "m1", "og", "H", 10, goal=True, own_goal=1)
    _shot(db, "m1", "late", "H", 50, xg=0.4)
    st = build_report._score_states(db, LEAGUE)
    assert st["A"]["min"][0] == pytest.approx(80), "A led from the tenth minute"
    assert st["H"]["for"] == pytest.approx([0, 0, 0.4]), "H chased it"


def test_penalties_and_own_goals_carry_no_chances(db):
    _match(db, "m1")
    _shot(db, "m1", "p", "H", 30, xg=0.79, situation="Penalty")
    _shot(db, "m1", "r", "H", 40, xg=0.1)
    st = build_report._score_states(db, LEAGUE)
    assert sum(st["H"]["for"]) == pytest.approx(0.1)


def test_a_thin_state_draws_no_point(db):
    """Under two matches' worth of minutes, the rate is not shown."""
    clubs = ["H", "A", "C", "D"]
    for n in range(3):
        _match(db, f"m{n}", "H", "A", hs=1)
        _shot(db, f"m{n}", f"g{n}", "H", 85, goal=True)   # H ahead 5 min a match
        _match(db, f"n{n}", "C", "D")
        _shot(db, f"n{n}", f"x{n}", "C", 30)
    html = build_report.score_state_block(db, LEAGUE)
    h = html[html.index("</span> H</p>"):]
    h = h[:h.index("</svg>")]
    assert "H, ahead:" not in h, "15 minutes ahead is not a rate"
    assert "ws-ax ws-thin" in h
    assert "H, level:" in h


def test_the_big_five_have_no_block(db):
    assert build_report.score_state_block(db, "Premier League") == ""


def test_teams_tab_carries_it(db):
    _match(db, "m1", hs=1)
    _shot(db, "m1", "s1", "H", 30, goal=True)
    src = open(build_report.__file__, encoding="utf-8").read()
    assert "shot_profile(db, lg) + score_state_block(db, lg)" in src
