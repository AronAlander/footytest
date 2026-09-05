"""Things that must hold of the HTML however odd the numbers get.

The nightly build is one process: an exception anywhere in it takes the
index page, both sets of archives and the prediction log with it. So the
arithmetic tests here are not about getting a pretty answer out of a silly
input, they are about getting any answer at all.
"""
import build_report
from conftest import LEAGUE, a_season, add_player, add_shot

NASTY = "<script>alert(1)</script>"
AMPER = "Bodø & Glimt"


def _with_nasty_names(db):
    a_season(db)
    db.execute("UPDATE fotmob_match_players SET player_name = ? "
               "WHERE player_id = 'AIK-0'", (NASTY,))
    db.execute("UPDATE fotmob_match_players SET team = ? WHERE team = 'AIK'",
               (AMPER,))
    db.execute("UPDATE fotmob_team_matches SET team = ? WHERE team = 'AIK'",
               (AMPER,))
    build_report.scope_to_current_season(db)
    return db


def test_names_are_escaped_everywhere_they_appear(db):
    """Including inside an SVG <title>, which is still markup."""
    _with_nasty_names(db)
    html = build_report.defensive_map(db, LEAGUE)
    assert NASTY not in html
    assert "&lt;script&gt;" in html
    assert "Bodø &amp; Glimt" in html
    assert "Glimt&" not in html, "an ampersand was written raw"


def test_keeper_names_are_escaped(db):
    a_season(db)
    db.execute("UPDATE fotmob_match_players SET player_name = ? "
               "WHERE is_gk = 1 AND player_id = 'gk-AIK'", (NASTY,))
    build_report.scope_to_current_season(db)
    html = build_report.keeper_table(db, LEAGUE)
    assert NASTY not in html and "&lt;script&gt;" in html


def test_two_builds_of_one_database_are_identical(db):
    """CLAUDE.md requires archive pages be byte-identical between builds.

    Anything that iterates a dict built from an unordered query, or sorts on
    a key that ties, quietly breaks this and shows up as a churning diff.
    """
    a_season(db)
    build_report.scope_to_current_season(db)
    first = (build_report.defensive_map(db, LEAGUE),
             build_report.keeper_table(db, LEAGUE))
    second = (build_report.defensive_map(db, LEAGUE),
              build_report.keeper_table(db, LEAGUE))
    assert first == second


def test_players_tied_on_everything_still_rank_stably(db):
    """Ten identical players must come out in the same order twice."""
    a_season(db)
    db.execute("UPDATE fotmob_match_players SET tackles = 2, "
               "interceptions = 1, clearances = 2, blocks = 1, recoveries = 2,"
               " aerials_won = 1 WHERE is_gk = 0")
    build_report.scope_to_current_season(db)
    order = lambda: [m["name"] for m in
                     build_report.defender_rows(db, LEAGUE)[0]]
    assert order() == order()
    assert build_report.defensive_map(db, LEAGUE) == \
        build_report.defensive_map(db, LEAGUE)


def test_a_keeper_who_faced_nothing_all_season_does_not_divide_by_zero(db):
    a_season(db)
    db.execute("UPDATE fotmob_match_players SET saves = 0, goals_conceded = 0,"
               " goals_prevented = 0 WHERE is_gk = 1")
    build_report.scope_to_current_season(db)
    keepers, _ = build_report.keeper_rows(db, LEAGUE)
    assert all(k["save_pct"] is None for k in keepers)
    html = build_report.keeper_table(db, LEAGUE)
    assert html and "–" in html, "the empty cell should be an en dash"


def test_every_defensive_count_zero_still_draws(db):
    """A bar scaled against a maximum of nought must not divide by it."""
    a_season(db)
    db.execute("UPDATE fotmob_match_players SET tackles = 0, "
               "interceptions = 0, clearances = 0, blocks = 0, recoveries = 0,"
               " aerials_won = 0 WHERE is_gk = 0")
    build_report.scope_to_current_season(db)
    assert build_report.defensive_map(db, LEAGUE)


def test_a_club_with_no_expected_goals_does_not_kill_the_build(db):
    """This one really did take down a nightly: a division by a zero xG sum."""
    a_season(db)
    for n in range(6):
        for club in ("AIK", "GAIS"):
            add_shot(db, 1000 + n, club, xg=0.0, outcome="Miss",
                     shot_id=f"z{n}-{club}")
    build_report.scope_to_current_season(db)
    html = build_report.shot_profile(db, LEAGUE)      # must not raise
    assert "Shot profile" in html, "the block bailed out instead of drawing"
    assert "a chance every" not in html, \
        "it compared two clubs with no chance between them"


def test_one_club_alone_makes_no_comparison(db):
    """Nothing should compare a club with itself."""
    a_season(db)
    for n in range(6):
        add_shot(db, 1000 + n, "AIK", xg=0.4, shot_id=f"y{n}")
    db.execute("DELETE FROM fotmob_team_matches WHERE team = 'GAIS'")
    build_report.scope_to_current_season(db)
    html = build_report.shot_profile(db, LEAGUE)
    assert "Shot profile" in html
    assert "a chance every" not in html, "GAIS was compared with GAIS"


def test_a_substitute_on_for_one_minute_does_not_explode_the_rates(db):
    """Per-90 on a tiny denominator is why the minutes bar exists."""
    a_season(db)
    add_player(db, 1000, "cameo", "Cameo Man", "AIK", minutes=1.0,
               tackles=1.0, interceptions=0.0, clearances=0.0, blocks=0.0,
               recoveries=0.0, aerials_won=0.0)
    build_report.scope_to_current_season(db)
    men, _ = build_report.defender_rows(db, LEAGUE)
    assert "Cameo Man" not in [m["name"] for m in men]
