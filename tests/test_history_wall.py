"""The history wall: where every club finished, every season stored."""
import re

import build_report
from conftest import add_understat_matches

LG = "Premier League"


def _season(db, season, table, played=None):
    """table: {club: points per match}; every club plays `played` matches."""
    n = played if played is not None else 2 * (len(table) - 1)
    for club, pts in table.items():
        add_understat_matches(db, club, n, league=LG, season=season, pts=pts)


def _cells(html, club):
    row = html[html.index(f"<th class='hw-club'>{club}</th>"):]
    row = row[:row.index("</tr>")]
    return re.findall(r"<td class='(hw-[^']*)'[^>]*>(\d*)</td>", row)


def test_positions_and_seasons_outside_the_division(db):
    _season(db, "2024", {"A": 3, "B": 2, "C": 1, "D": 0})
    _season(db, "2025", {"A": 0, "B": 3, "C": 1, "E": 2})
    html = build_report.history_wall(db, LG)
    assert [c[1] for c in _cells(html, "A")] == ["1", "4"]
    assert _cells(html, "D") == [("hw-c", "4"), ("hw-gap", "")], "D went down"
    assert _cells(html, "E") == [("hw-gap", ""), ("hw-c", "2")], "E came up"


def test_ever_presents_come_first(db):
    _season(db, "2024", {"Up": 3, "Stay": 1, "B": 0, "C": 0})
    _season(db, "2025", {"Stay": 1, "B": 0, "C": 0, "New": 3})
    html = build_report.history_wall(db, LG)
    rows = re.findall(r"<th class='hw-club'>([^<]+)</th>", html)
    assert rows.index("Stay") < rows.index("Up")
    assert rows.index("Stay") < rows.index("New")


def test_one_season_is_not_a_history(db):
    _season(db, "2025", {"A": 3, "B": 1, "C": 0})
    assert build_report.history_wall(db, LG) == ""


def test_a_season_in_progress_is_marked(db):
    _season(db, "2024", {"A": 3, "B": 1, "C": 0})
    _season(db, "2025", {"A": 3, "B": 1, "C": 0}, played=1)
    html = build_report.history_wall(db, LG)
    assert "so far" in html
    assert all("hw-now" in cls for cls, _p in _cells(html, "A")[-1:])
    assert "hw-now" not in _cells(html, "A")[0][0]


def test_a_finished_season_is_not(db):
    _season(db, "2024", {"A": 3, "B": 1, "C": 0})
    _season(db, "2025", {"A": 3, "B": 1, "C": 0})
    assert "hw-now" not in build_report.history_wall(db, LG)


def test_tooltip_carries_points_against_expected(db):
    _season(db, "2024", {"A": 3, "B": 1, "C": 0})
    _season(db, "2025", {"A": 0, "B": 1, "C": 3})
    html = build_report.history_wall(db, LG)
    # the fixture's xPts is 1.3 a match, over four matches 5.2
    assert "A, 2024/25: 1st of 3 · 12 pts from 4 · +6.8 against expected" in html
    assert "A, 2025/26: 3rd of 3 · 0 pts from 4 · −5.2 against expected" in html


def test_teams_tab_carries_it():
    src = open(build_report.__file__, encoding="utf-8").read()
    assert "lgview(lg, history_wall(db, lg), i == 0)" in src
