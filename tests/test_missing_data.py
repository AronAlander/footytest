"""What the blocks do when the feed sends nothing.

This is the file that earns its keep. Every bug found in review on these
boards was the same bug: a number the feed omitted was read as a nought,
the page published a confident wrong answer, and nothing anywhere said so.
The rule these tests hold to is that a missing figure must either be
provably a nought or must remove the block from the page -- never quietly
become zero.
"""
import build_report
from conftest import LEAGUE, a_season, add_keeper, add_match, add_player


def test_keeper_with_nothing_to_do_counts_as_nought(db):
    """A clean sheet with no saves in it is a real nought, and stays."""
    a_season(db)
    db.execute("UPDATE fotmob_match_players SET saves = 0, goals_conceded = 0,"
               " goals_prevented = NULL WHERE is_gk = 1")
    build_report.scope_to_current_season(db)
    keepers, dropped = build_report.keeper_rows(db, LEAGUE)
    assert dropped == 0, "a quiet afternoon is not a missing figure"
    assert len(keepers) == 2
    assert all(k["prevented"] == 0 for k in keepers)


def test_keeper_figures_all_missing_removes_the_board(db):
    """The feed renaming its labels must not publish a league of noughts.

    Every keeper row arrives with three NULLs. Read as "he faced nothing"
    they would all rank at +0.00 in alphabetical order, which is a wrong
    answer no reader could tell from a right one.
    """
    a_season(db)
    db.execute("UPDATE fotmob_match_players SET saves = NULL, "
               "goals_conceded = NULL, goals_prevented = NULL WHERE is_gk = 1")
    build_report.scope_to_current_season(db)
    keepers, dropped = build_report.keeper_rows(db, LEAGUE)
    assert keepers == []
    assert dropped == 12
    assert build_report.keeper_table(db, LEAGUE) == ""


def test_keeper_missing_one_line_is_marked_not_silently_short(db):
    """A busy match with no figure is dropped, counted, and shown as dropped."""
    a_season(db)
    db.execute("UPDATE fotmob_match_players SET goals_prevented = NULL "
               "WHERE is_gk = 1 AND player_id = 'gk-AIK' AND match_id = '1000'")
    build_report.scope_to_current_season(db)
    keepers, dropped = build_report.keeper_rows(db, LEAGUE)
    assert dropped == 1
    short = [k for k in keepers if k["dropped"]]
    assert len(short) == 1
    assert short[0]["apps"] == 5, "the dropped line is out of his totals too"
    assert " *" in build_report.keeper_table(db, LEAGUE)


def test_outfield_counts_all_missing_removes_the_board(db):
    """Same rule on the defensive board: no tackles column, no picture."""
    a_season(db)
    db.execute("UPDATE fotmob_match_players SET tackles = NULL WHERE is_gk = 0")
    build_report.scope_to_current_season(db)
    men, dropped = build_report.defender_rows(db, LEAGUE)
    assert men == []
    assert dropped == 60
    assert build_report.defensive_map(db, LEAGUE) == ""


def test_outfielder_missing_his_position_is_dropped(db):
    """A line the parser did not understand is not a match he sat out."""
    a_season(db)
    db.execute("UPDATE fotmob_match_players SET position = NULL "
               "WHERE is_gk = 0 AND match_id = '1000'")
    build_report.scope_to_current_season(db)
    men, dropped = build_report.defender_rows(db, LEAGUE)
    assert dropped == 10
    assert all(m["apps"] == 5 for m in men)
    assert "10 match lines left out" in build_report.defensive_map(db, LEAGUE)


def test_boards_say_how_much_of_the_season_they_cover(db):
    """Teamsheets lag results, and a running total must not be called a season."""
    a_season(db, matches=6)
    add_match(db, 9999, "AIK", "GAIS")          # played, no teamsheet yet
    build_report.scope_to_current_season(db)
    for html in (build_report.keeper_table(db, LEAGUE),
                 build_report.defensive_map(db, LEAGUE)):
        assert "6 of 7 matches" in html


def test_no_league_no_block(db):
    """A big-five league has no per-player match rows at all."""
    a_season(db)
    build_report.scope_to_current_season(db)
    assert build_report.keeper_table(db, "Premier League") == ""
    assert build_report.defensive_map(db, "Premier League") == ""


def test_database_without_the_table_does_not_raise(db):
    """The production database is migrated, never rebuilt; guard, don't assume."""
    a_season(db)
    build_report.scope_to_current_season(db)
    db.execute("DROP VIEW fotmob_match_players")
    db.execute("DROP TABLE main.fotmob_match_players")
    assert build_report.keeper_table(db, LEAGUE) == ""
    assert build_report.defensive_map(db, LEAGUE) == ""


def test_too_few_players_draws_nothing(db):
    """Eight dots is the floor; below it the picture says nothing."""
    a_season(db, matches=6, clubs=("AIK", "GAIS"))
    db.execute("DELETE FROM fotmob_match_players WHERE is_gk = 0 "
               "AND player_id NOT IN ('AIK-0', 'AIK-1', 'GAIS-0')")
    build_report.scope_to_current_season(db)
    men, _ = build_report.defender_rows(db, LEAGUE)
    assert len(men) == 3
    assert build_report.defensive_map(db, LEAGUE) == ""
