"""Fetch big-five-league advanced stats (xG, xA, xPts, PPDA, ...) from Understat.

Understat's league pages load their data from a public JSON endpoint that
needs no key or signup; this pulls it directly (one request per league) and
stores it in football.sqlite alongside the basic match data.

Note: Understat covers only the big five leagues plus Russia - Allsvenskan
has no free xG source.

Usage:
    python fetch_understat.py                # current season (auto-detected)
    python fetch_understat.py 2018 2019      # specific seasons (starting year)
    python fetch_understat.py --backfill     # every season Understat has (2014+)
"""

import gzip
import html
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# display name (matches fetch_data.py / the report) -> Understat URL slug
LEAGUES = {
    "Serie A": "Serie_A",
    "Premier League": "EPL",
    "La Liga": "La_liga",
    "Bundesliga": "Bundesliga",
    "Ligue 1": "Ligue_1",
}
# Understat labels seasons by starting year (2025 = 2025/26); the autumn-spring
# campaigns start in August, so the season flips automatically on 1 August
_NOW = datetime.now()
SEASON = str(_NOW.year if _NOW.month >= 8 else _NOW.year - 1)
FIRST_SEASON = 2014  # Understat's history starts with 2014/15
REQUEST_PAUSE = 2.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "X-Requested-With": "XMLHttpRequest",
}

PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"
DB_PATH = PROJECT_DIR / "football.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS understat_players (
    season       TEXT NOT NULL,
    league       TEXT NOT NULL,
    player_id    TEXT NOT NULL,
    player_name  TEXT,
    team         TEXT,
    position     TEXT,
    games        INTEGER,
    minutes      INTEGER,
    goals        INTEGER,
    xg           REAL,
    assists      INTEGER,
    xa           REAL,
    shots        INTEGER,
    key_passes   INTEGER,
    npg          INTEGER,
    npxg         REAL,
    xg_chain     REAL,
    xg_buildup   REAL,
    fetched_at   TEXT,
    PRIMARY KEY (season, league, player_id)
);
CREATE TABLE IF NOT EXISTS understat_team_matches (
    season        TEXT NOT NULL,
    league        TEXT NOT NULL,
    team          TEXT NOT NULL,
    match_date    TEXT NOT NULL,
    home_away     TEXT,
    xg            REAL,
    xga           REAL,
    npxg          REAL,
    npxga         REAL,
    ppda          REAL,
    ppda_allowed  REAL,
    deep          INTEGER,
    deep_allowed  INTEGER,
    scored        INTEGER,
    missed        INTEGER,
    xpts          REAL,
    result        TEXT,
    pts           INTEGER,
    npxgd         REAL,
    fetched_at    TEXT,
    PRIMARY KEY (season, league, team, match_date)
);
"""


def migrate_if_needed(db):
    """Older databases lack the league column (they were Serie A only).
    The data is fully refetchable, so just rebuild those tables."""
    for table in ("understat_players", "understat_team_matches"):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
        if cols and "league" not in cols:
            print(f"Migrating {table}: no league column, dropping for refetch.")
            db.execute(f"DROP TABLE {table}")
    db.executescript(SCHEMA)


def ppda_ratio(value):
    """Understat encodes PPDA as {'att': passes, 'def': defensive actions}."""
    if isinstance(value, dict) and value.get("def"):
        return round(value["att"] / value["def"], 2)
    return None


def fetch_league(db, league, slug, season, fetched_at):
    url = f"https://understat.com/getLeagueData/{slug}/{season}"
    print(f"Fetching {url} ...")
    headers = dict(HEADERS, Referer=f"https://understat.com/league/{slug}/{season}")
    request = urllib.request.Request(url, headers=headers)
    raw = urllib.request.urlopen(request, timeout=60).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    data = json.loads(raw)

    (DATA_DIR / f"understat_{slug.lower()}_{season}.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )

    players = data.get("players") or []
    for p in players:
        db.execute(
            "INSERT OR REPLACE INTO understat_players VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                # some names arrive entity-encoded ("M&#039;Bala Nzola")
                season, league, p["id"], html.unescape(p.get("player_name") or ""),
                html.unescape(p.get("team_title") or ""),
                p.get("position"), int(p.get("games") or 0), int(p.get("time") or 0),
                int(p.get("goals") or 0), float(p.get("xG") or 0),
                int(p.get("assists") or 0), float(p.get("xA") or 0),
                int(p.get("shots") or 0), int(p.get("key_passes") or 0),
                int(p.get("npg") or 0), float(p.get("npxG") or 0),
                float(p.get("xGChain") or 0), float(p.get("xGBuildup") or 0),
                fetched_at,
            ),
        )

    match_count = 0
    for team in (data.get("teams") or {}).values():
        for match in team.get("history") or []:
            db.execute(
                "INSERT OR REPLACE INTO understat_team_matches VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    season, league, team["title"], match.get("date"), match.get("h_a"),
                    float(match.get("xG") or 0), float(match.get("xGA") or 0),
                    float(match.get("npxG") or 0), float(match.get("npxGA") or 0),
                    ppda_ratio(match.get("ppda")), ppda_ratio(match.get("ppda_allowed")),
                    int(match.get("deep") or 0), int(match.get("deep_allowed") or 0),
                    int(match.get("scored") or 0), int(match.get("missed") or 0),
                    float(match.get("xpts") or 0), match.get("result"),
                    int(match.get("pts") or 0), float(match.get("npxGD") or 0),
                    fetched_at,
                ),
            )
            match_count += 1
    print(f"  {league}: {len(players)} players, {match_count} team-match rows")


def seasons_from_args(argv):
    if "--backfill" in argv:
        return [str(y) for y in range(FIRST_SEASON, int(SEASON) + 1)]
    explicit = [a for a in argv if not a.startswith("-")]
    if explicit:
        bad = [a for a in explicit if not a.isdigit() or not FIRST_SEASON <= int(a) <= int(SEASON)]
        if bad:
            raise SystemExit(f"Season must be a starting year {FIRST_SEASON}-{SEASON}, got: {' '.join(bad)}")
        return explicit
    return [SEASON]


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    migrate_if_needed(db)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    seasons = seasons_from_args(sys.argv[1:])
    for season in seasons:
        print(f"--- season {season}/{int(season) % 100 + 1} ---")
        for league, slug in LEAGUES.items():
            fetch_league(db, league, slug, season, fetched_at)
            db.commit()
            time.sleep(REQUEST_PAUSE)

    totals = db.execute(
        "SELECT league, COUNT(*) FROM understat_players GROUP BY league"
    ).fetchall()
    print(f"\nDatabase: {DB_PATH.name}")
    for league, count in totals:
        print(f"  {league}: {count} players")
    db.close()


if __name__ == "__main__":
    main()
