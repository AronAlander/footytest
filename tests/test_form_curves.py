"""The form curves at the start of a season.

The curves used to need a full five-match window and one match more before
a club was drawn at all. On 28 September, six rounds into the big-five
seasons, the published page had all twenty La Liga clubs, two of Serie A's,
one of Ligue 1's and none from the Premier League or the Bundesliga. An
expanding window fixes it: a club is drawn from its second match, and the
points built on fewer than five matches are marked as such.
"""
import re

import build_report
from build_report import ROLLING_WINDOW
from conftest import LEAGUE, add_understat_matches


def _panel(html, team):
    """One club's panel from the rendered block."""
    start = html.index(f"{team}<span class='val")
    return html[start:html.index("</svg></div>", start)]


def test_every_club_is_drawn_three_rounds_into_a_season(db):
    """The reported bug: most clubs missing from the chart."""
    clubs = [f"Club {n}" for n in range(10)]
    for club in clubs:
        add_understat_matches(db, club, 3)
    html = build_report.rolling_sparklines(db, LEAGUE)
    for club in clubs:
        assert f"{club}<span class='val" in html, f"{club} is missing"


def test_the_window_grows_until_it_is_full(db):
    """Point i averages the last min(i + 1, window) matches."""
    xgd = [1.0, 0.0, 2.0, -1.0, 3.0, 4.0, -2.0]
    for n, v in enumerate(xgd):
        db.execute(
            "INSERT INTO understat_team_matches (season, league, team, "
            "match_date, home_away, npxgd, pts) VALUES (?,?,?,?,?,?,?)",
            ("2026", LEAGUE, "Sirius", f"2026-04-{n + 1:02d}", "h", v, 1))
    add_understat_matches(db, "Other", 7)
    html = build_report.rolling_sparklines(db, LEAGUE)
    shown = [float(v.replace("−", "-")) for v in re.findall(
        r"<title>matchday \d+: ([+\-−]?[\d.]+)", _panel(html, "Sirius"))]
    expected = []
    for i in range(len(xgd)):
        win = xgd[max(0, i - ROLLING_WINDOW + 1):i + 1]
        expected.append(round(sum(win) / len(win), 2))
    assert shown == expected


def test_a_club_short_of_a_full_window_is_drawn_dashed_only(db):
    add_understat_matches(db, "Early", ROLLING_WINDOW - 2)
    add_understat_matches(db, "Settled", ROLLING_WINDOW + 3)
    html = build_report.rolling_sparklines(db, LEAGUE)
    early = _panel(html, "Early")
    assert "spark-partial" in early
    assert re.search(r"class='spark-line up' ", early) is None, \
        "no solid stretch before the window has filled"
    settled = _panel(html, "Settled")
    assert "spark-partial" in settled and re.search(
        r"class='spark-line up' ", settled), "dashed start, then solid"


def test_the_points_before_the_window_fills_say_how_many_matches(db):
    add_understat_matches(db, "Early", 3)
    add_understat_matches(db, "Other", 3)
    panel = _panel(build_report.rolling_sparklines(db, LEAGUE), "Early")
    assert "(over 2 matches)" in panel
    assert panel.count("spark-dot up open") + panel.count("spark-dot down open") == 3
