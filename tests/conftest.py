"""Fixtures: a real schema, filled with a season small enough to reason about.

The tests never touch football.sqlite. That database lives in the Actions
cache, is never rebuilt, and is not in the repository, so a suite that read
it would pass here and be unrunnable in CI -- and would test today's feed
rather than the rules the code is supposed to hold to. Instead every test
builds the genuine schema out of the fetchers and inserts rows it chose,
which is the only way to write down "the feed sent nothing" as a case.
"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import build_report                       # noqa: E402
import fetch_data                         # noqa: E402
import fetch_fotmob                       # noqa: E402
import fetch_preseason                    # noqa: E402
import fetch_understat                    # noqa: E402

LEAGUE = "Allsvenskan"

# the six counts every outfield line must carry, and the shape of a keeper's
PLAYER_COLS = (
    "season league match_id player_id team player_name started is_gk shirt "
    "position minutes rating goals assists tackles interceptions clearances "
    "blocks recoveries aerials_won saves goals_conceded goals_prevented"
).split()


@pytest.fixture
def db():
    """An empty database with the schema the fetchers actually create."""
    con = sqlite3.connect(":memory:")
    for mod in (fetch_data, fetch_understat, fetch_fotmob, fetch_preseason):
        con.executescript(mod.SCHEMA)
    fetch_fotmob.migrate(con)
    return con


def add_match(db, match_id, home, away, season="2026", league=LEAGUE,
              home_goals=1, away_goals=0):
    """One fixture, from both sides, the way fetch_fotmob stores it."""
    for team, opp, side, scored, missed in (
        (home, away, "h", home_goals, away_goals),
        (away, home, "a", away_goals, home_goals),
    ):
        db.execute(
            "INSERT INTO fotmob_team_matches (season, league, match_id, team, "
            "opponent, match_date, home_away, scored, missed, possession) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (season, league, str(match_id), team, opp, f"{season}-04-01",
             side, scored, missed, 50.0),
        )


def add_player(db, match_id, player_id, name, team, season="2026",
               league=LEAGUE, **stats):
    """One match line. Anything not named is left NULL, as the feed leaves it."""
    row = {
        "season": season, "league": league, "match_id": str(match_id),
        "player_id": str(player_id), "team": team, "player_name": name,
        "started": 1, "is_gk": 0, "shirt": "5", "position": "1",
        "minutes": 90.0, "rating": 7.0, "goals": 0, "assists": 0,
        "tackles": 2.0, "interceptions": 1.0, "clearances": 3.0,
        "blocks": 1.0, "recoveries": 4.0, "aerials_won": 1.0,
        "saves": None, "goals_conceded": None, "goals_prevented": None,
    }
    row.update(stats)
    cols = ",".join(PLAYER_COLS)
    marks = ",".join("?" * len(PLAYER_COLS))
    db.execute(f"INSERT INTO fotmob_match_players ({cols}) VALUES ({marks})",
               [row[c] for c in PLAYER_COLS])


def add_keeper(db, match_id, player_id, name, team, saves, conceded,
               prevented, **stats):
    """A goalkeeper's line: no defensive counts, three keeper ones."""
    add_player(db, match_id, player_id, name, team, is_gk=1, position="11",
               shirt="1", tackles=None, interceptions=None, clearances=None,
               blocks=None, recoveries=None, aerials_won=None,
               saves=saves, goals_conceded=conceded, goals_prevented=prevented,
               **stats)


def add_shot(db, match_id, team, season="2026", league=LEAGUE, own_goal=0,
             outcome="Goal", xg=0.3, shot_id=None):
    db.execute(
        "INSERT INTO fotmob_match_shots (season, league, match_id, shot_id, "
        "team, minute, x, y, xg, outcome, is_own_goal, is_blocked, "
        "is_on_target) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (season, league, str(match_id), shot_id or f"s{match_id}-{team}-{own_goal}",
         team, 30, 90.0, 34.0, xg, outcome, own_goal, 0, 1),
    )


def a_season(db, matches=6, clubs=("AIK", "GAIS"), season="2026"):
    """A whole small season: every club fields a keeper and enough outfielders.

    Six matches at 90 minutes clears the 450-minute bar exactly, which is the
    point -- the boards' entry rule is a real edge and the fixture sits on it.
    """
    for n in range(matches):
        mid = 1000 + n
        add_match(db, mid, clubs[0], clubs[1], season=season)
        for club in clubs:
            add_keeper(db, mid, f"gk-{club}", f"Keeper {club}", club,
                       saves=3.0, conceded=1.0, prevented=0.2, season=season)
            # five a side, so the board clears its own "too few to draw" bar
            for i in range(5):
                add_player(db, mid, f"{club}-{i}", f"Player {i} {club}", club,
                           season=season, position=str(1 + i % 3),
                           tackles=float(i + 1), interceptions=1.0,
                           clearances=float(5 - i), blocks=1.0,
                           recoveries=float(i + 2), aerials_won=1.0)
    db.execute(
        "INSERT INTO fotmob_players (season, league, player_id, player_name, "
        "team, minutes, matches) VALUES (?,?,?,?,?,?,?)",
        (season, LEAGUE, "gk-AIK", "Keeper AIK", "AIK", matches * 90, matches),
    )
    db.commit()
    return db


@pytest.fixture
def season(db):
    """The small season above, unscoped."""
    return a_season(db)


@pytest.fixture
def scoped(season):
    """The small season as the live page sees it."""
    build_report.scope_to_current_season(season)
    return season
