"""The "Every way the season could go" fan.

The lines are played out in the browser and checked there by
browser_smoke.py. What can be pinned here is the part a reader would
catch out: the headline percentages must be the projection table's own,
over every simulation, not a recount of the sample the lines happen to
draw — two different "top 4" figures for one club on one page would be
worse than either being slightly off.
"""
import json
import re

import build_report
from conftest import LEAGUE
from test_projection_trend import _fixtures


def _payload(html):
    raw = re.search(r"class='fan-data'>(.*?)</script>", html, re.S).group(1)
    return json.loads(raw.replace("<\/", "</"))


def test_the_fan_is_drawn_for_a_season_still_being_played(db):
    _fixtures(db)
    html = build_report.season_projection_fan(db, LEAGUE)
    assert "Every way the season could go" in html
    assert "class='card fan-card'" in html


def test_a_finished_season_has_no_fan(db):
    _fixtures(db, played_rounds=6, rounds=6)
    assert build_report.season_projection_fan(db, LEAGUE) == ""


def test_the_headline_is_the_projection_tables_own_figure(db):
    _fixtures(db)
    r = build_report._compute_projection(db, LEAGUE)
    fan = _payload(build_report.season_projection_fan(db, LEAGUE))
    for i in range(r["n"]):
        assert fan["europe"][i] == round(100 * r["europe"][i] / r["sims"])
        assert fan["drop"][i] == round(100 * r["drop"][i] / r["sims"])
    assert fan["sims"] == r["sims"], "and it says how many it counted"


def test_it_opens_on_the_club_whose_top_four_place_is_most_in_doubt(db):
    _fixtures(db)
    r = build_report._compute_projection(db, LEAGUE)
    html = build_report.season_projection_fan(db, LEAGUE)
    fan = _payload(html)
    doubt = [abs(r["europe"][i] / r["sims"] - 0.5) for i in range(r["n"])]
    assert doubt[fan["focus"]] == min(doubt)
    assert f"value='{fan['focus']}' selected" in html


def test_the_picker_lists_every_club_in_projected_order(db):
    _fixtures(db)
    r = build_report._compute_projection(db, LEAGUE)
    html = build_report.season_projection_fan(db, LEAGUE)
    listed = [int(v) for v in re.findall(r"<option value='(\d+)'", html)]
    assert listed == list(r["order"])


def test_it_ships_no_fixture_list_of_its_own(db):
    """The seasons are played from the simulator's payload, not a copy."""
    _fixtures(db)
    fan = _payload(build_report.season_projection_fan(db, LEAGUE))
    assert "fixtures" not in fan and "basePts" not in fan


def test_the_league_tab_carries_it_beside_the_simulator(db):
    """Built but never placed would pass every other test here."""
    _fixtures(db)
    # the standings table on the same tab needs points and expected points,
    # which the projection fixture has no reason to fill
    db.execute("UPDATE fotmob_team_matches SET "
               "pts = CASE WHEN scored > missed THEN 3 WHEN scored = missed "
               "THEN 1 ELSE 0 END, xpts = 1.4")
    db.commit()
    build_report.scope_to_current_season(db)
    html = build_report.league_section(db, LEAGUE)
    assert html.index("Every way the season could go") < \
        html.index("Simulate one season"), "and before the simulator"
