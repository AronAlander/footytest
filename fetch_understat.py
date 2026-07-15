"""Fetch Serie A advanced stats (xG, xA, xPts, PPDA, ...) from Understat.

Understat's league pages load their data from a public JSON endpoint that
needs no key or signup; this pulls it directly (one request per run) and
stores it in football.sqlite alongside the basic match data.

Note: Understat covers only the big five leagues plus Russia, so this is
Serie A only - Allsvenskan has no free xG source.

Usage:
    python fetch_understat.py
"""

import gzip
import json
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LEAGUE = "Serie_A"
SEASON = "2025"  # Understat labels seasons by starting year: 2025 = 2025/26

URL = f"https://understat.com/getLeagueData/{LEAGUE}/{SEASON}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"https://understat.com/league/{LEAGUE}/{SEASON}",
}

PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"
DB_PATH = PROJECT_DIR / "football.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS understat_players (
    season       TEXT NOT NULL,
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
    PRIMARY KEY (season, player_id)
);
CREATE TABLE IF NOT EXISTS understat_team_matches (
    season        TEXT NOT NULL,
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
    PRIMARY KEY (season, team, match_date)
);
"""


def ppda_ratio(value):
    """Understat encodes PPDA as {'att': passes, 'def': defensive actions}."""
    if isinstance(value, dict) and value.get("def"):
        return round(value["att"] / value["def"], 2)
    return None


def main() -> None:
    print(f"Fetching {URL} ...")
    request = urllib.request.Request(URL, headers=HEADERS)
    raw = urllib.request.urlopen(request, timeout=60).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    data = json.loads(raw)

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / f"understat_{LEAGUE.lower()}_{SEASON}.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )

    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    players = data.get("players") or []
    for p in players:
        db.execute(
            "INSERT OR REPLACE INTO understat_players VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                SEASON, p["id"], p.get("player_name"), p.get("team_title"),
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
    teams = data.get("teams") or {}
    for team in teams.values():
        for match in team.get("history") or []:
            db.execute(
                "INSERT OR REPLACE INTO understat_team_matches VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    SEASON, team["title"], match.get("date"), match.get("h_a"),
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

    db.commit()
    print(f"Stored {len(players)} players and {match_count} team-match rows "
          f"for {LEAGUE} {SEASON} in {DB_PATH.name}")
    db.close()


if __name__ == "__main__":
    main()
