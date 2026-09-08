"""The team comparison radar's underlying numbers.

The axis list itself lives in the page's JavaScript and is not reachable
from here; what is reachable is `load_teams`, which has to supply the value
the new axis is drawn from and to hand back nothing rather than a nonsense
number when the feed cannot support it.
"""
import build_report
from conftest import LEAGUE
from test_fixture_tape import BIG5, understat_match


def a_club(db, team, matches=6, **stats):
    for n in range(matches):
        understat_match(db, team, "h" if n % 2 else "a", n=n, **stats)
    db.commit()


def test_chance_quality_is_expected_goals_per_deep_completion(db):
    a_club(db, "Club A", npxg=1.5, deep=6.0)
    club = build_report.load_teams(db, BIG5)[0]
    assert club["quality"] == round(1.5 / 6.0, 3)


def test_a_club_that_never_reaches_the_final_third_has_no_quality(db):
    """Dividing by no deep completions is how the nightly used to die."""
    a_club(db, "Club A", npxg=1.5, deep=0.0)
    club = build_report.load_teams(db, BIG5)[0]
    assert club["quality"] is None
    assert club["npxg"] == 1.5, "the rest of the row survives"


def test_a_fotmob_league_has_no_quality_and_says_so_with_a_null(db):
    """Allsvenskan has no deep completions at all, so the axis drops out.

    The page filters an axis out when any selected club is missing it, which
    only works if the value arrives as null rather than as a zero.
    """
    from conftest import add_understat_matches
    add_understat_matches(db, "AIK", 6)          # no ppda, no deep
    club = build_report.load_teams(db, LEAGUE)[0]
    assert club["quality"] is None
    assert club["ppda"] is None
    assert club["deep"] is None


def test_territory_survives_even_though_it_left_the_chart(db):
    """It moved to the table underneath, so it still has to be loaded."""
    a_club(db, "Club A", npxg=1.5, deep=6.0, deep_allowed=4.0)
    club = build_report.load_teams(db, BIG5)[0]
    assert club["deep"] == 6.0
    assert club["deep_allowed"] == 4.0
