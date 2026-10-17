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
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API_KEY = "123"  # TheSportsDB public test key, fine for development
BASE_URL = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}"
# Measured on 2026-10-17, not taken from documentation. The test key allows
# about ten requests a minute: at 2.1s apart it threw 429 after 9 to 11
# requests every time, four times running, and at 6.2s apart 26 in a row
# went through. The old comment here said thirty a minute, which may once
# have been true and is not now -- the nightly started failing on the 16th.
REQUEST_PAUSE = 6.2
# A throttle is a fixed two-minute lockout: it cleared after 120.5, 120.6,
# 120.7 and 120.6 seconds, and asking again during it neither shortens nor
# extends it. So one wait just past it is all the patience that helps; the
# 20/40/80 ladder this replaces only ever succeeded on its third wait, and
# 60 of its 140 seconds were spent waiting inside a lockout it could not end.
RETRY_WAIT = 125
RETRY_ATTEMPTS = 2
# The key in BASE_URL is TheSportsDB's public test key, so the rate limit is
# shared with every other project using it: a 429 says the world was busy,
# not that we asked too fast. Pacing alone cannot avoid it, which is why a
# round we fail to reach falls back on what is already stored rather than
# stopping the night -- see fetch_league.
MAX_UNREACHED = 3    # ... but this many unreachable rounds is a real outage
# A round whose every fixture has a result, the last of them more than this
# many days ago, is not asked for again on an ordinary night: nothing in it
# is going to change, and at ten requests a minute every request is worth
# 6.2 seconds of a run that has a time limit. A late correction to an old
# result is still caught, by the full sweep below.
SETTLED_DAYS = 7
FULL_SWEEP_WEEKDAY = 0   # Monday: every round of every league is re-checked

LEAGUES = {
    # Allsvenskan is back (2026-07-17): FotMob supplies its xG via
    # fetch_fotmob.py, TheSportsDB the results and standings as usual
    "Allsvenskan": {
        "id": "4347",
        # Allsvenskan runs over a calendar year
        "season": str(date.today().year),
        "rounds": 30,
    },
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


def fetch_json(url: str, retries: int = RETRY_ATTEMPTS) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "football-analytics/0.3"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < retries - 1:
                print(f"    rate limited, waiting {RETRY_WAIT}s for the lockout to clear...")
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


def forget_superseded(db, league_name, season, round_number, event_ids):
    """Drop fixtures the feed has stopped listing for a round it still fills.

    A complete round that no longer names a fixture we stored has not been
    truncated -- the feed has re-issued it. Ligue 1 round 23 replaced
    "Rennes v Paris Saint-Germain" with the same match at the other venue
    under a fresh id, which is housekeeping, not a broken response.

    Treating it as fatal wedged the nightly permanently rather than for a
    night: the superseded row stayed in the table, so every later run found
    it missing from the feed again and failed the same check.

    A fixture that has been PLAYED is kept instead of deleted, and named in
    the output. Losing a stored result to a feed hiccup would be worse than
    carrying a duplicate for a day, and it is rare enough to be worth a
    human looking at it.
    """
    dropped, kept = [], []
    for event_id in event_ids:
        row = db.execute(
            "SELECT home_score, away_score FROM matches WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row and (row[0] is not None or row[1] is not None):
            kept.append(event_id)
            continue
        db.execute("DELETE FROM matches WHERE event_id = ?", (event_id,))
        dropped.append(event_id)
    if dropped:
        print(f"  round {round_number}: {len(dropped)} fixture(s) re-issued by "
              f"the feed, forgetting {', '.join(dropped)}")
    if kept:
        print(f"  round {round_number}: ! the feed no longer lists "
              f"{', '.join(kept)}, which already had a result — kept")
    return dropped, kept


def settled_rounds(db, league_name, season, today):
    """Rounds that are finished, and finished long enough ago to leave alone."""
    cutoff = (today - timedelta(days=SETTLED_DAYS)).isoformat()
    rows = db.execute(
        """SELECT round FROM matches
           WHERE league = ? AND season = ? AND round IS NOT NULL
           GROUP BY round
           HAVING SUM(home_score IS NULL OR away_score IS NULL) = 0
              AND MAX(match_date) < ?""",
        (league_name, season, cutoff)).fetchall()
    return {r[0] for r in rows}


def fetch_league(db: sqlite3.Connection, league_name: str, league: dict,
                 fetched_at: str, full: bool = True, today=None) -> None:
    print(f"\n=== {league_name} (season {league['season']}) ===")
    slug = league_name.lower().replace(" ", "_")

    table_url = f"{BASE_URL}/lookuptable.php?l={league['id']}&s={league['season']}"
    # paced like every round: without it this follows the previous league's
    # last round at once, an eleventh request inside the minute
    time.sleep(REQUEST_PAUSE)
    try:
        payload = fetch_json(table_url)
        (DATA_DIR / f"{slug}_table.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        count = save_standings(db, league_name, league["season"], payload.get("table"))
        print(f"  standings: {count} teams (test key truncates the table)")
    except Exception as error:
        print(f"  standings: FAILED ({error})")

    all_events = []
    total = with_result = 0
    failures = []
    unreached = []
    skip = (set() if full else
            settled_rounds(db, league_name, league["season"], today or date.today()))
    asked = 0
    for round_number in range(1, league["rounds"] + 1):
        if round_number in skip:
            continue
        asked += 1
        time.sleep(REQUEST_PAUSE)
        url = f"{BASE_URL}/eventsround.php?id={league['id']}&r={round_number}&s={league['season']}"
        try:
            payload = fetch_json(url)
            if "events" not in payload or not isinstance(payload["events"], (list, type(None))):
                raise ValueError("invalid events response")
            events = payload["events"] or []
            # an empty round is never a re-issue, whatever the arithmetic
            # below makes of it: a league that has a round has fixtures in it
            if not events:
                raise ValueError("no fixtures in response")
            if any(not e.get("idEvent") or not e.get("strHomeTeam") or
                   not e.get("strAwayTeam") for e in events):
                raise ValueError("incomplete fixture identity")
            known = {str(r[0]) for r in db.execute(
                "SELECT event_id FROM matches WHERE league=? AND season=? AND round=?",
                (league_name, league["season"], round_number))}
            received = {str(e["idEvent"]) for e in events}
            # Double round-robin schedules have (rounds + 2) / 4 fixtures
            # per round. This catches truncated new rounds as well as old ones.
            expected = (league["rounds"] + 2) // 4
            if len(received) < expected or len(received) != len(events):
                raise ValueError(f"expected {expected} distinct fixtures, got {len(received)}")
            superseded = sorted(known - received)
            if superseded:
                # the round is full (checked above), so these have been
                # replaced rather than dropped
                forget_superseded(db, league_name, league["season"],
                                  round_number, superseded)
        except Exception as error:
            # A round that answered with something wrong is a failure: the
            # response is evidence, and bad evidence must not reach the
            # database. A round we could not reach at all is different --
            # usually the shared test key being throttled -- and there is
            # no evidence either way. Falling back on the fixtures already
            # stored for it costs nothing: they are last night's answer to
            # the same question, and the upsert below only ever adds or
            # updates, so leaving them alone leaves them intact.
            # Only a network-level error counts as "could not reach".
            # Anything else -- a malformed response, or a bug in the checks
            # above -- is evidence, and must stop the run rather than be
            # mistaken for a busy afternoon. URLError, HTTPError and
            # TimeoutError are all OSError; our own checks raise ValueError.
            if not isinstance(error, OSError):
                print(f"  round {round_number}: FAILED ({error})")
                failures.append(round_number)
                continue
            stored = db.execute(
                "SELECT COUNT(*) FROM matches WHERE league=? AND season=? AND round=?",
                (league_name, league["season"], round_number)).fetchone()[0]
            if not stored:
                # never seen it, so there is nothing to fall back on
                print(f"  round {round_number}: FAILED ({error}; nothing stored)")
                failures.append(round_number)
                continue
            print(f"  round {round_number}: unreachable ({error}), "
                  f"keeping {stored} stored fixture(s)")
            unreached.append(round_number)
            continue
        all_events.extend(events)
        with_result += sum(1 for e in events if e.get("intHomeScore") is not None)

    # nothing new is only a fault if something was asked for: a league whose
    # every round is settled has nothing to fetch, and that is the point
    if failures or (asked and not all_events):
        db.rollback()
        raise RuntimeError(f"{league_name}: incomplete fixture fetch; failed rounds {failures}")
    if len(unreached) > MAX_UNREACHED:
        # a handful of throttled rounds is a busy afternoon; this many is an
        # outage, and publishing as though the schedule were fresh would be
        # a lie about how current the page is
        db.rollback()
        raise RuntimeError(
            f"{league_name}: {len(unreached)} rounds unreachable "
            f"({', '.join(map(str, unreached))}); refusing to publish stale fixtures")
    total = upsert_matches(db, league_name, all_events, fetched_at)

    (DATA_DIR / f"{slug}_rounds.json").write_text(
        json.dumps({"events": all_events}, indent=2), encoding="utf-8"
    )
    stale = (f", {len(unreached)} round(s) kept from last run"
             if unreached else "")
    if skip:
        stale += f", {len(skip)} settled round(s) not re-checked"
    print(f"  rounds 1-{league['rounds']}: {total} matches upserted "
          f"({with_result} with results{stale})")


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    today = date.today()
    full = "--full" in sys.argv or today.weekday() == FULL_SWEEP_WEEKDAY
    print("full sweep: every round re-checked" if full else
          f"settled rounds skipped (finished over {SETTLED_DAYS} days ago); "
          "the full sweep runs on Mondays or with --full")
    for league_name, league in LEAGUES.items():
        fetch_league(db, league_name, league, fetched_at, full=full, today=today)
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
