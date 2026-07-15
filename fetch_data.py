"""Fetch Allsvenskan and Serie A data from TheSportsDB (free test key, no signup)
and store it in a local SQLite database.

Uses only the Python standard library. Each run upserts matches and appends a
dated standings snapshot, so history accumulates over time. Raw API responses
are also kept in data/ for debugging.

Usage:
    python fetch_data.py
"""

import json
import sqlite3
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

API_KEY = "123"  # TheSportsDB public test key, fine for development
BASE_URL = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}"

LEAGUES = {
    "Allsvenskan": {
        "id": "4347",
        # Allsvenskan runs over a calendar year
        "season": str(date.today().year),
    },
    "Serie A": {
        "id": "4332",
        # Serie A runs autumn-spring
        "season": "2025-2026",
    },
}

ENDPOINTS = {
    "table": "lookuptable.php?l={id}&s={season}",
    "past_events": "eventspastleague.php?id={id}",
    "next_events": "eventsnextleague.php?id={id}",
    "season_events": "eventsseason.php?id={id}&s={season}",
}

PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"
DB_PATH = PROJECT_DIR / "football.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    event_id    TEXT PRIMARY KEY,
    league      TEXT NOT NULL,
    season      TEXT,
    round       INTEGER,
    match_date  TEXT,
    match_time  TEXT,
    home_team   TEXT,
    away_team   TEXT,
    home_score  INTEGER,
    away_score  INTEGER,
    status      TEXT,
    fetched_at  TEXT
);
CREATE TABLE IF NOT EXISTS standings (
    snapshot_date  TEXT NOT NULL,
    league         TEXT NOT NULL,
    season         TEXT,
    rank           INTEGER,
    team           TEXT NOT NULL,
    played         INTEGER,
    wins           INTEGER,
    draws          INTEGER,
    losses         INTEGER,
    goals_for      INTEGER,
    goals_against  INTEGER,
    goal_diff      INTEGER,
    points         INTEGER,
    PRIMARY KEY (snapshot_date, league, team)
);
"""


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "football-analytics/0.2"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def upsert_matches(db: sqlite3.Connection, league: str, events: list, fetched_at: str) -> int:
    count = 0
    for event in events or []:
        if not event.get("idEvent"):
            continue
        db.execute(
            """INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(event_id) DO UPDATE SET
                 round=excluded.round, match_date=excluded.match_date,
                 match_time=excluded.match_time, home_score=excluded.home_score,
                 away_score=excluded.away_score, status=excluded.status,
                 fetched_at=excluded.fetched_at""",
            (
                event["idEvent"], league, event.get("strSeason"),
                to_int(event.get("intRound")), event.get("dateEvent"),
                event.get("strTime"), event.get("strHomeTeam"),
                event.get("strAwayTeam"), to_int(event.get("intHomeScore")),
                to_int(event.get("intAwayScore")), event.get("strStatus"),
                fetched_at,
            ),
        )
        count += 1
    return count


def save_standings(db: sqlite3.Connection, league: str, season: str, rows: list) -> int:
    today = date.today().isoformat()
    count = 0
    for row in rows or []:
        team = row.get("strTeam")
        if not team:
            continue
        db.execute(
            "INSERT OR REPLACE INTO standings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                today, league, season, to_int(row.get("intRank")), team,
                to_int(row.get("intPlayed")), to_int(row.get("intWin")),
                to_int(row.get("intDraw")), to_int(row.get("intLoss")),
                to_int(row.get("intGoalsFor")), to_int(row.get("intGoalsAgainst")),
                to_int(row.get("intGoalDifference")), to_int(row.get("intPoints")),
            ),
        )
        count += 1
    return count


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for league_name, league in LEAGUES.items():
        print(f"\n=== {league_name} (season {league['season']}) ===")
        for endpoint_name, endpoint_template in ENDPOINTS.items():
            url = f"{BASE_URL}/{endpoint_template.format(**league)}"
            try:
                payload = fetch_json(url)
            except Exception as error:
                print(f"  {endpoint_name}: FAILED ({error})")
                continue

            slug = league_name.lower().replace(" ", "_")
            (DATA_DIR / f"{slug}_{endpoint_name}.json").write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )

            if endpoint_name == "table":
                count = save_standings(db, league_name, league["season"], payload.get("table"))
                print(f"  {endpoint_name}: {count} teams -> standings snapshot")
            else:
                events = next((v for v in payload.values() if isinstance(v, list)), None)
                count = upsert_matches(db, league_name, events, fetched_at)
                print(f"  {endpoint_name}: {count} matches upserted")

    db.commit()
    totals = db.execute(
        "SELECT league, COUNT(*), SUM(home_score IS NOT NULL) FROM matches GROUP BY league"
    ).fetchall()
    print(f"\nDatabase: {DB_PATH.name}")
    for league_name, total, finished in totals:
        print(f"  {league_name}: {total} matches stored ({finished or 0} with results)")
    db.close()


if __name__ == "__main__":
    main()
