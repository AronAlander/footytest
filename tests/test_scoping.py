"""A block that reads a whole season must never read the wrong one.

Every per-match table is stored for every season at once, and nothing in a
query filters on season: the temp views do it. So a new block that reads one
of those tables a season at a time is correct on the live page by accident
and wrong on every archive page, which is a bug you cannot see without
building an archive. These tests build them.

The subtler half is which table the view anchors on. A match is stored when
it is played; its teamsheets and its shotmap arrive later. A view that asks
its own table for the newest season it holds will, on the morning after a
season opens, still answer with the whole of last season -- and draw it
under this season's heading.
"""
import build_report
from conftest import LEAGUE, a_season, add_match


def test_archive_page_carries_no_fotmob_board(db):
    a_season(db)
    build_report.scope_to_archive_season(db, "2024")
    assert build_report.keeper_table(db, LEAGUE) == ""
    assert build_report.defensive_map(db, LEAGUE) == ""


def test_frozen_season_sees_only_its_own_rows(db):
    a_season(db, season="2026")
    a_season(db, matches=6, season="2025")
    build_report.scope_to_fotmob_season(db, LEAGUE, "2025")
    men, _ = build_report.defender_rows(db, LEAGUE)
    keepers, _ = build_report.keeper_rows(db, LEAGUE)
    assert len(men) == 10 and len(keepers) == 2
    # 2025's rows only: six matches each, not the twelve both seasons hold
    assert {m["apps"] for m in men} == {6}
    assert {k["apps"] for k in keepers} == {6}


def test_live_page_sees_only_the_newest_season(db):
    a_season(db, season="2026")
    a_season(db, matches=6, season="2025")
    build_report.scope_to_current_season(db)
    men, _ = build_report.defender_rows(db, LEAGUE)
    assert {m["apps"] for m in men} == {6}, "two seasons were summed together"


def test_new_season_before_its_teamsheets_draws_nothing(db):
    """The anchor bug, written down.

    A 2027 match exists; not one teamsheet for it has arrived. The boards
    must go quiet. Anchored on their own table they would instead find 2026
    the newest season with player rows and draw all of it under 2027.
    """
    a_season(db, season="2026")
    add_match(db, 7001, "AIK", "GAIS", season="2027")
    build_report.scope_to_current_season(db)
    men, _ = build_report.defender_rows(db, LEAGUE)
    keepers, _ = build_report.keeper_rows(db, LEAGUE)
    assert men == [] and keepers == []
    assert build_report.defensive_map(db, LEAGUE) == ""
    assert build_report.keeper_table(db, LEAGUE) == ""


def test_scoping_shadows_every_per_match_table(db):
    """The views must cover both per-match tables, not just the older one."""
    a_season(db)
    build_report.scope_to_current_season(db)
    shadowed = {r[0] for r in db.execute(
        "SELECT name FROM temp.sqlite_master WHERE type = 'view'")}
    assert {"fotmob_match_players", "fotmob_match_shots"} <= shadowed
