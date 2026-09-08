"""The fixture card's tale of the tape.

The block exists to answer one question — what do these two do at the venue
they are about to play at — so the tests are mostly about it refusing to
answer when it cannot: too few matches at that venue, too few clubs to rank
against, a feed that never carried the column.
"""
import build_report
from conftest import LEAGUE

BIG5 = "Premier League"


def team_match(db, team, side, tag=0, season="2026", league=LEAGUE, **stats):
    """One row of fotmob_team_matches, only the columns the tape reads."""
    row = {"npxg": 1.5, "npxga": 1.0, "tackles": 14.0, "interceptions": 6.0,
           "xg": 1.6, "xg_set_play": 0.4}
    row.update(stats)
    cols = ["season", "league", "match_id", "team", "home_away"] + list(row)
    vals = [season, league, f"{team}-{side}-{tag}", team, side] + \
        list(row.values())
    db.execute(
        f"INSERT INTO fotmob_team_matches ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})", vals)


def understat_match(db, team, side, season="2026", league=BIG5, n=0, **stats):
    row = {"npxg": 1.5, "npxga": 1.0, "ppda": 10.0, "deep": 6.0}
    row.update(stats)
    cols = ["season", "league", "team", "match_date", "home_away"] + list(row)
    vals = [season, league, team, f"{season}-04-{n + 1:02d}", side] + \
        list(row.values())
    db.execute(
        f"INSERT INTO understat_team_matches ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})", vals)


def a_fotmob_league(db, clubs=10, per_venue=6, **stats):
    for i in range(clubs):
        for side in ("h", "a"):
            for n in range(per_venue):
                team_match(db, f"Club {i}", side, tag=n,
                           npxg=1.0 + i * 0.1, **stats)
    db.commit()


def an_understat_league(db, clubs=10, per_venue=6):
    for i in range(clubs):
        for side in ("h", "a"):
            for n in range(per_venue):
                understat_match(db, f"Club {i}", side,
                                n=n if side == "h" else n + 20,
                                npxg=1.0 + i * 0.1, ppda=15.0 - i)
    db.commit()


def test_enough_matches_and_clubs_gives_a_tape(db):
    a_fotmob_league(db)
    t = build_report.fixture_tape(db, LEAGUE)
    assert [m[0] for m in t["m"]] == ["Attack", "Defence", "Ball-winning",
                                      "Set plays"]
    assert len(t["h"]) == 10 and len(t["a"]) == 10


def test_a_club_short_of_matches_at_that_venue_is_left_out(db):
    """Four home matches is not a home record worth ranking."""
    a_fotmob_league(db)
    for n in range(6):
        team_match(db, "Newcomer", "h", tag=n)      # only 6 home, 0 away
    db.commit()
    t = build_report.fixture_tape(db, LEAGUE)
    assert "Newcomer" in t["h"]
    assert "Newcomer" not in t["a"], "no away record, so no away ranking"


def test_too_few_matches_anywhere_draws_nothing(db):
    """August: everyone has played four times, so no percentile means much.

    Four is written down rather than derived from FIXTURE_TAPE_MIN. Deriving
    it made the test move with the constant, so lowering the bar to one match
    still passed -- a test that cannot fail is worse than no test.
    """
    a_fotmob_league(db, per_venue=4)
    assert build_report.fixture_tape(db, LEAGUE) == {}


def test_the_bar_has_not_been_quietly_lowered(db):
    """The gate is a judgement, and moving it should be a deliberate edit."""
    assert build_report.FIXTURE_TAPE_MIN >= 5
    assert build_report.FIXTURE_TAPE_POOL >= 8


def test_the_big_five_are_split_by_venue_too(db):
    """The venue split is the point of the block, on both feeds."""
    an_understat_league(db)
    for side, xg in (("h", 9.0), ("a", 0.1)):
        for n in range(6):
            understat_match(db, "Fortress", side,
                            n=n if side == "h" else n + 20, npxg=xg)
    db.commit()
    t = build_report.fixture_tape(db, BIG5)
    assert t["h"]["Fortress"][0][1] == 100
    assert t["a"]["Fortress"][0][1] == 0


def test_too_few_clubs_draws_nothing(db):
    a_fotmob_league(db, clubs=build_report.FIXTURE_TAPE_POOL - 1)
    assert build_report.fixture_tape(db, LEAGUE) == {}


def test_the_two_venues_are_ranked_apart(db):
    """A club good only at home must rank high at home and low away."""
    a_fotmob_league(db)
    for side, xg in (("h", 9.0), ("a", 0.1)):
        for n in range(6):
            team_match(db, "Fortress", side, tag=n, npxg=xg)
    db.commit()
    t = build_report.fixture_tape(db, LEAGUE)
    assert t["h"]["Fortress"][0][1] == 100
    assert t["a"]["Fortress"][0][1] == 0


def test_conceding_less_ranks_higher(db):
    """Defence is inverted: the low number is the good one."""
    a_fotmob_league(db)
    for n in range(6):
        team_match(db, "Solid", "h", tag=n, npxga=0.05)
        team_match(db, "Leaky", "h", tag=n, npxga=9.0)
        for t in ("Solid", "Leaky"):
            team_match(db, t, "a", tag=n)
    db.commit()
    t = build_report.fixture_tape(db, LEAGUE)
    assert t["h"]["Solid"][1][1] == 100
    assert t["h"]["Leaky"][1][1] == 0


def test_only_the_first_two_rows_claim_a_better_end(db):
    """Pressing high and taking corners are styles, not merits."""
    a_fotmob_league(db)
    merit = [m[3] for m in build_report.fixture_tape(db, LEAGUE)["m"]]
    assert merit == [True, True, False, False]


def test_the_big_five_get_understat_metrics(db):
    an_understat_league(db)
    t = build_report.fixture_tape(db, BIG5)
    assert [m[0] for m in t["m"]] == ["Attack", "Defence", "Pressing",
                                      "Chance quality"]
    # PPDA is inverted too: the club allowing fewest passes presses most
    assert t["h"]["Club 9"][2][1] == 100


def test_a_club_missing_one_figure_keeps_the_others(db):
    """No deep completions means no chance quality, not no club."""
    an_understat_league(db)
    db.execute("UPDATE understat_team_matches SET deep = 0 "
               "WHERE team = 'Club 3'")
    t = build_report.fixture_tape(db, BIG5)
    assert t["h"]["Club 3"][3] == [None, None]
    assert t["h"]["Club 3"][0][0] is not None
    assert len(t["h"]) == 10, "the club stays in the block"


def test_a_database_without_the_table_does_not_raise(db):
    a_fotmob_league(db)
    db.execute("DROP TABLE main.fotmob_team_matches")
    assert build_report.fixture_tape(db, LEAGUE) == {}
