"""Fetch preseason friendlies for all six leagues' clubs from FotMob.

Uses the same unofficial FotMob JSON endpoints as fetch_fotmob.py (no key,
no signing - can break without notice): one request per league for the team
list, then one request per team for its fixture list, keeping everything
tagged as a friendly (FotMob files club friendlies, summer series and the
like under tournament id 489 / "Club Friendlies").

Friendlies carry no xG on FotMob - scores, dates and opponents only - so
this feeds a results/fixtures view, never the analytics tabs.

Usage:
    python fetch_preseason.py

~125 requests at 1.8 s spacing, roughly four minutes. Matches are upserted
by (league, match id); re-runs refresh scores and pick up new fixtures.
"""

import gzip
import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# FotMob league ids (name printed per league so a wrong id is obvious)
LEAGUES = {
    "Serie A": 55,
    "Premier League": 47,
    "La Liga": 87,
    "Bundesliga": 54,
    "Ligue 1": 53,
    "Allsvenskan": 67,
}
FRIENDLY_LEAGUE_ID = 489
REQUEST_PAUSE = 1.8

BASE = "https://www.fotmob.com/api/data"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
}

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "football.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS preseason_matches (
    league      TEXT NOT NULL,
    match_id    TEXT NOT NULL,
    match_date  TEXT,
    match_time  TEXT,
    home_team   TEXT,
    away_team   TEXT,
    home_score  INTEGER,
    away_score  INTEGER,
    finished    INTEGER,
    tournament  TEXT,
    fetched_at  TEXT,
    PRIMARY KEY (league, match_id)
);
"""


def get_json(url):
    request = urllib.request.Request(url, headers=HEADERS)
    raw = urllib.request.urlopen(request, timeout=40).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def league_team_ids(league_json):
    """Team id -> name from the standings table (fixture lists are empty for
    a big-five season that hasn't kicked off yet, the table never is)."""
    teams = {}

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            if (len(node) >= 10 and all(
                    isinstance(r, dict) and r.get("id") and r.get("name")
                    and "pageUrl" in r for r in node)):
                for row in node:
                    teams[row["id"]] = row["name"]
            else:
                for value in node:
                    walk(value)

    walk(league_json.get("table") or [])
    if not teams:  # mid-season leagues: fall back to the fixture list
        for m in (league_json.get("matches") or {}).get("allMatches") or []:
            for side in ("home", "away"):
                t = m.get(side) or {}
                if t.get("id"):
                    teams[t["id"]] = t.get("name")
    return teams


def is_friendly(fixture):
    tournament = fixture.get("tournament") or {}
    name = str(tournament.get("name") or "")
    return tournament.get("leagueId") == FRIENDLY_LEAGUE_ID or "friendl" in name.lower()


def store_fixture(db, league, fixture, fetched_at):
    status = fixture.get("status") or {}
    if status.get("cancelled"):
        return False
    home, away = fixture.get("home") or {}, fixture.get("away") or {}
    if not (fixture.get("id") and home.get("name") and away.get("name")):
        return False
    utc = str(status.get("utcTime") or "")
    finished = 1 if status.get("finished") else 0
    db.execute(
        "INSERT OR REPLACE INTO preseason_matches VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            league, str(fixture["id"]), utc[:10], utc[11:16],
            home["name"], away["name"],
            home.get("score") if finished else None,
            away.get("score") if finished else None,
            finished, (fixture.get("tournament") or {}).get("name"),
            fetched_at,
        ),
    )
    return True


def fetch_league(db, league, league_id, fetched_at):
    league_json = get_json(f"{BASE}/leagues?id={league_id}")
    name = (league_json.get("details") or {}).get("name")
    teams = league_team_ids(league_json)
    print(f"--- {league} (FotMob: {name}) - {len(teams)} teams ---")
    time.sleep(REQUEST_PAUSE)

    stored = 0
    for team_id, team_name in teams.items():
        try:
            team_json = get_json(f"{BASE}/teams?id={team_id}")
        except Exception as error:
            print(f"  ! {team_name} skipped: {error}")
            time.sleep(REQUEST_PAUSE)
            continue
        fixtures = ((team_json.get("fixtures") or {}).get("allFixtures") or {}).get("fixtures") or []
        stored += sum(
            store_fixture(db, league, f, fetched_at) for f in fixtures if is_friendly(f)
        )
        time.sleep(REQUEST_PAUSE)
    db.commit()
    count = db.execute(
        "SELECT COUNT(*), SUM(finished) FROM preseason_matches WHERE league = ?", (league,)
    ).fetchone()
    print(f"  friendlies stored: {count[0]} total ({count[1] or 0} finished)")


def main() -> None:
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for league, league_id in LEAGUES.items():
        fetch_league(db, league, league_id, fetched_at)

    print(f"\nDatabase: {DB_PATH.name}")
    for row in db.execute(
        "SELECT league, COUNT(*), SUM(finished) FROM preseason_matches "
        "GROUP BY league ORDER BY league"
    ):
        print(f"  {row[0]}: {row[1]} friendlies ({row[2] or 0} finished)")
    db.close()


if __name__ == "__main__":
    main()
