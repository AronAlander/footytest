"""Fetch big-five-league data from TheSportsDB (free test key, no signup)
and store it in a local SQLite database.

Uses only the Python standard library. Matches are fetched round by round
(the test key truncates season/recent-results endpoints, but serves complete
rounds), so the whole current season lands in the database. Each run upserts
matches and appends a dated standings snapshot, so history accumulates.

Usage:
    python fetch_data.py
"""

import json
import sqlite3
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

API_KEY = "123"  # TheSportsDB public test key, fine for development
BASE_URL = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}"
REQUEST_PAUSE = 2.1  # test key allows ~30 requests/minute
RETRY_WAIT = 35      # seconds to back off after HTTP 429

LEAGUES = {
    # Allsvenskan is on hold while the project focuses on Serie A analytics
    # (no free xG source exists for it) - uncomment to fetch it again:
    # "Allsvenskan": {
    #     "id": "4347",
    #     # Allsvenskan runs over a calendar year
    #     "season": str(date.today().year),
    #     "rounds": 30,
    # },
    # The big five European leagues, all autumn-spring; SEASON below flips
    # to the new campaign automatically on 1 August, no manual bump needed
    "Serie A": {"id": "4332", "season": None, "rounds": 38},
    "Premier League": {"id": "4328", "season": None, "rounds": 38},
    "La Liga": {"id": "4335", "season": None, "rounds": 38},
    "Bundesliga": {"id": "4331", "season": None, "rounds": 34},
    "Ligue 1": {"id": "4334", "season": None, "rounds": 34},
}
_START_YEAR = date.today().year if date.today().month >= 8 else date.today().year - 1
SEASON = f"{_START_YEAR}-{_START_YEAR + 1}"
for _league in LEAGUES.values():
    if _league["season"] is None:
        _league["season"] = SEASON

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


def fetch_json(url: str, retries: int = 3) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "football-analytics/0.3"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < retries - 1:
                print(f"    rate limited, waiting {RETRY_WAIT}s...")
                time.sleep(RETRY_WAIT)
            else:
                raise


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


def fetch_league(db: sqlite3.Connection, league_name: str, league: dict, fetched_at: str) -> None:
    print(f"\n=== {league_name} (season {league['season']}) ===")
    slug = league_name.lower().replace(" ", "_")

    table_url = f"{BASE_URL}/lookuptable.php?l={league['id']}&s={league['season']}"
    try:
        payload = fetch_json(table_url)
        (DATA_DIR / f"{slug}_table.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        count = save_standings(db, league_name, league["season"], payload.get("table"))
        print(f"  standings: {count} teams (test key truncates the table)")
    except Exception as error:
        print(f"  standings: FAILED ({error})")

    all_events = []
    total = with_result = 0
    for round_number in range(1, league["rounds"] + 1):
        time.sleep(REQUEST_PAUSE)
        url = f"{BASE_URL}/eventsround.php?id={league['id']}&r={round_number}&s={league['season']}"
        try:
            events = fetch_json(url).get("events") or []
        except Exception as error:
            print(f"  round {round_number}: FAILED ({error})")
            continue
        all_events.extend(events)
        total += upsert_matches(db, league_name, events, fetched_at)
        with_result += sum(1 for e in events if e.get("intHomeScore") is not None)

    (DATA_DIR / f"{slug}_rounds.json").write_text(
        json.dumps({"events": all_events}, indent=2), encoding="utf-8"
    )
    print(f"  rounds 1-{league['rounds']}: {total} matches upserted ({with_result} with results)")


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for league_name, league in LEAGUES.items():
        fetch_league(db, league_name, league, fetched_at)
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
