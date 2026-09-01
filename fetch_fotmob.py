"""Fetch Allsvenskan advanced stats (xG, xA, shots on target, ...) from FotMob.

FotMob has no official API, but its website loads JSON from two hosts that
need no key or signing (verified 2026-08-31):

  www.fotmob.com/api/data/leagues?id=67   league table, fixtures, stat links
  data.fotmob.com/stats/67/season/<id>/*  full player leaderboards per stat
  www.fotmob.com/api/data/matchDetails    per-match team stats incl. xG

Being unofficial it can change or disappear without notice - this script
fails loudly rather than storing partial garbage. Player season stats reach
back to 2017; match details exist for any match id in the fixture lists.

Unlike Understat there is no PPDA, deep completions, xGChain/xGBuildup or
per-player npxG - but per-match npxG, xGOT and shots on target exist, which
Understat doesn't offer. xPts is not provided either, so it is computed here
with a Poisson model over each match's xG (documented in the report).

Usage:
    python fetch_fotmob.py               # current season (calendar year)
    python fetch_fotmob.py 2024 2025     # specific seasons
    python fetch_fotmob.py --backfill    # every season with stats (2017+)

Matches already stored are skipped, so re-runs only fetch new results
(~2 s per new match; a full season is ~240 matches, roughly 8 minutes) --
except that a match stored before the current stat set (STATS_VERSION) is
re-fetched newest first, up to REFRESH_PER_RUN per run, so new columns fill
in over a few nights instead of blowing the workflow's time budget. That
applies to the seasons being fetched, which unattended means the current one:
`python fetch_fotmob.py --backfill --refresh-all` is how every stored season
gets there, and it has to be run by hand.
"""

import gzip
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from math import exp, factorial
from pathlib import Path

LEAGUE_ID = 67
LEAGUE = "Allsvenskan"
SEASON = str(date.today().year)  # Allsvenskan runs over a calendar year
FIRST_SEASON = 2017              # oldest season with FotMob stat links
REQUEST_PAUSE = 1.8

BASE = "https://www.fotmob.com/api/data"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
}

PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"
DB_PATH = PROJECT_DIR / "football.sqlite"

# data.fotmob.com stat file -> fotmob_players column. Minutes, goals, assists,
# xG, xA, xGOT and chances created are season totals in StatValue; shots and
# shots on target are per-90 rates, converted to totals via minutes below.
STAT_FILES = {
    "mins_played": "minutes",
    "goals": "goals",
    "goal_assist": "assists",
    "expected_goals": "xg",
    "expected_assists": "xa",
    "expected_goalsontarget": "xgot",
    "total_scoring_att": "shots_per90",
    "ontarget_scoring_att": "sot_per90",
    "total_att_assist": "chances_created",
}

# Everything else FotMob measures for a match, as
# (database column, payload key, kind). Kind picks the parse: "n" is a plain
# number, "pct" the percentage inside a string like "338 (80%)" -- the count
# in front of it comes from its own column.
#
# One row per team holds that team's own figures only. The opponent's are in
# the opponent's row, which every reader of this table already joins to; the
# older xga/shots_allowed mirror columns predate that and are not extended.
MATCH_STATS = [
    ("big_chances",        "big_chance",                 "n"),
    ("big_chances_missed", "big_chance_missed_title",    "n"),
    ("touches_opp_box",    "touches_opp_box",            "n"),
    ("corners",            "corners",                    "n"),
    ("shots_inside_box",   "shots_inside_box",           "n"),
    ("shots_outside_box",  "shots_outside_box",          "n"),
    ("blocked_shots",      "blocked_shots",              "n"),
    ("woodwork",           "shots_woodwork",             "n"),
    ("xg_open_play",       "expected_goals_open_play",   "n"),
    ("xg_set_play",        "expected_goals_set_play",    "n"),
    ("passes",             "passes",                     "n"),
    ("accurate_passes",    "accurate_passes",            "n"),
    ("pass_pct",           "accurate_passes",            "pct"),
    ("offsides",           "Offsides",                   "n"),
    ("tackles",            "matchstats.headers.tackles", "n"),
    ("interceptions",      "interceptions",              "n"),
    ("blocks",             "shot_blocks",                "n"),
    ("clearances",         "clearances",                 "n"),
    ("keeper_saves",       "keeper_saves",               "n"),
    ("duels_won",          "duel_won",                   "n"),
    ("aerials_won",        "aerials_won",                "n"),
    ("dribbles",           "dribbles_succeeded",         "n"),
    ("fouls",              "fouls",                      "n"),
    ("yellow_cards",       "yellow_cards",               "n"),
    ("red_cards",          "red_cards",                  "n"),
]

# Per player per match, as (column, the label FotMob gives it). A label a
# player has no entry for stores NULL: an outfielder has no Saves, and a
# defender who never shot has no xG -- neither of them has a zero.
PLAYER_STATS = [
    ("minutes",             "Minutes played"),
    ("rating",              "FotMob rating"),
    ("goals",               "Goals"),
    ("assists",             "Assists"),
    ("xg",                  "Expected goals (xG)"),
    ("npxg",                "xG Non-penalty"),
    ("xgot",                "Expected goals on target (xGOT)"),
    ("xa",                  "Expected assists (xA)"),
    ("shots",               "Total shots"),
    ("sot",                 "Shots on target"),
    ("chances_created",     "Chances created"),
    ("big_chances_created", "Big chances created"),
    ("touches",             "Touches"),
    ("touches_opp_box",     "Touches in opposition box"),
    ("accurate_passes",     "Accurate passes"),
    ("tackles",             "Tackles"),
    ("interceptions",       "Interceptions"),
    ("clearances",          "Clearances"),
    ("blocks",              "Blocks"),
    ("recoveries",          "Recoveries"),
    ("duels_won",           "Duels won"),
    ("aerials_won",         "Aerial duels won"),
    ("dribbles",            "Successful dribbles"),
    ("fouls",               "Fouls committed"),
    ("was_fouled",          "Was fouled"),
    ("dispossessed",        "Dispossessed"),
    ("saves",               "Saves"),
    ("goals_conceded",      "Goals conceded"),
    ("goals_prevented",     "Goals prevented"),
]

# Bumped when the set above changes: a stored match carrying an older number
# is re-fetched, newest first, so new columns fill in without a cold start.
# (The database lives in the Actions cache and the nightly run only asks for
# matches it has never seen, so nothing else would ever revisit them.)
STATS_VERSION = 4
REFRESH_PER_RUN = 120   # ...but only this many per run: re-fetching every
                        # stored season at once is half an hour of requests
                        # and the workflow has 45 minutes for everything
FAIL_STREAK = 8         # consecutive match failures that mean the feed has
                        # changed rather than one match being odd

SCHEMA = """
CREATE TABLE IF NOT EXISTS fotmob_players (
    season          TEXT NOT NULL,
    league          TEXT NOT NULL,
    player_id       TEXT NOT NULL,
    player_name     TEXT,
    team            TEXT,
    minutes         INTEGER,
    matches         INTEGER,
    goals           INTEGER,
    xg              REAL,
    assists         INTEGER,
    xa              REAL,
    xgot            REAL,
    shots           INTEGER,
    shots_on_target INTEGER,
    chances_created INTEGER,
    fetched_at      TEXT,
    PRIMARY KEY (season, league, player_id)
);
CREATE TABLE IF NOT EXISTS fotmob_match_players (
    season      TEXT NOT NULL,
    league      TEXT NOT NULL,
    match_id    TEXT NOT NULL,
    player_id   TEXT NOT NULL,
    team        TEXT,
    player_name TEXT,
    started     INTEGER,
    is_gk       INTEGER,
    shirt       TEXT,
    position    TEXT,
    minutes             REAL,
    rating              REAL,
    goals               REAL,
    assists             REAL,
    xg                  REAL,
    npxg                REAL,
    xgot                REAL,
    xa                  REAL,
    shots               REAL,
    sot                 REAL,
    chances_created     REAL,
    big_chances_created REAL,
    touches             REAL,
    touches_opp_box     REAL,
    accurate_passes     REAL,
    tackles             REAL,
    interceptions       REAL,
    clearances          REAL,
    blocks              REAL,
    recoveries          REAL,
    duels_won           REAL,
    aerials_won         REAL,
    dribbles            REAL,
    fouls               REAL,
    was_fouled          REAL,
    dispossessed        REAL,
    saves               REAL,
    goals_conceded      REAL,
    goals_prevented     REAL,
    fetched_at  TEXT,
    PRIMARY KEY (season, league, match_id, player_id)
);
CREATE TABLE IF NOT EXISTS fotmob_match_shots (
    season      TEXT NOT NULL,
    league      TEXT NOT NULL,
    match_id    TEXT NOT NULL,
    shot_id     TEXT NOT NULL,
    team        TEXT,
    player_id   TEXT,
    player_name TEXT,
    minute      INTEGER,
    minute_added INTEGER,
    period      TEXT,
    x           REAL,
    y           REAL,
    xg          REAL,
    xgot        REAL,
    outcome     TEXT,
    shot_type   TEXT,
    situation   TEXT,
    is_blocked  INTEGER,
    is_on_target INTEGER,
    is_own_goal INTEGER,
    inside_box  INTEGER,
    fetched_at  TEXT,
    PRIMARY KEY (season, league, match_id, shot_id)
);
CREATE TABLE IF NOT EXISTS fotmob_team_matches (
    season       TEXT NOT NULL,
    league       TEXT NOT NULL,
    match_id     TEXT NOT NULL,
    team         TEXT NOT NULL,
    opponent     TEXT,
    match_date   TEXT,
    home_away    TEXT,
    xg           REAL,
    xga          REAL,
    npxg         REAL,
    npxga        REAL,
    xgot         REAL,
    xgota        REAL,
    shots        INTEGER,
    shots_allowed INTEGER,
    sot          INTEGER,
    sot_allowed  INTEGER,
    possession   REAL,
    scored       INTEGER,
    missed       INTEGER,
    result       TEXT,
    pts          INTEGER,
    npxgd        REAL,
    xpts         REAL,
    fetched_at   TEXT,
    PRIMARY KEY (season, league, team, match_id)
);
"""


# sqlite has no ADD COLUMN IF NOT EXISTS, and executescript would abort the
# whole SCHEMA the second time an ALTER ran, so these live outside it and are
# applied one at a time against what the database actually has.
MIGRATIONS = {
    "fotmob_team_matches":
        [(column, "REAL") for column, _, _ in MATCH_STATS]
        + [("stats_version", "INTEGER")],
    # SCHEMA creates this one whole, but only when it does not exist yet: a
    # database holding an older version of the table would never gain a
    # column added to PLAYER_STATS afterwards
    "fotmob_match_players": [(column, "REAL") for column, _ in PLAYER_STATS],
    # nothing to add yet, and that is the point: the entry is here so the
    # first column this table gains has somewhere to be declared, rather
    # than being written into SCHEMA where the live database never sees it
    "fotmob_match_shots": [],
}


def migrate(db):
    """Add any column this code expects and the database lacks.

    The live database is never recreated -- it is restored from the Actions
    cache every night -- so this is the only way a new column ever reaches
    production.
    """
    for table, columns in MIGRATIONS.items():
        have = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue        # the table itself is SCHEMA's business
        added = [c for c, _ in columns if c not in have]
        for column, kind in columns:
            if column in added:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        if added:
            db.commit()
            print(f"  schema: {len(added)} new column(s) on {table}")


def get_json(url):
    request = urllib.request.Request(url, headers=HEADERS)
    raw = urllib.request.urlopen(request, timeout=40).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def poisson_xpts(xg_for, xg_against, max_goals=10):
    """Expected points from a match's xG, assuming independent Poisson goals.
    (Understat ships its own simulated xPts; FotMob has none, so this stands in.)"""
    pf = [exp(-xg_for) * xg_for ** k / factorial(k) for k in range(max_goals + 1)]
    pa = [exp(-xg_against) * xg_against ** k / factorial(k) for k in range(max_goals + 1)]
    win = sum(pf[i] * pa[j] for i in range(len(pf)) for j in range(len(pa)) if i > j)
    draw = sum(pf[i] * pa[i] for i in range(len(pf)))
    return round(3 * win + draw, 2)


def pct(value):
    """The percentage inside '264 (71%)'. None when there is no bracket."""
    if value is None or isinstance(value, (int, float)):
        return None
    text = str(value)
    if "(" not in text or "%" not in text:
        return None
    try:
        return float(text.split("(", 1)[1].split("%", 1)[0].strip().replace(",", "."))
    except ValueError:
        return None


def num(value):
    """FotMob mixes numbers and strings like '0.72' or '264 (71%)'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).split()[0].replace(",", "."))
    except ValueError:
        return None


def season_stat_id(league_json, season):
    for link in (league_json.get("stats") or {}).get("seasonStatLinks") or []:
        if str(link.get("Name")) == season:
            return link.get("TournamentId")
    return None


def fetch_players(db, season, stat_id, fetched_at):
    players = {}
    for slug, column in STAT_FILES.items():
        url = f"https://data.fotmob.com/stats/{LEAGUE_ID}/season/{stat_id}/{slug}.json"
        try:
            data = get_json(url)
        except Exception as error:
            print(f"  ! {slug}.json unavailable ({error}) - column left empty")
            time.sleep(REQUEST_PAUSE)
            continue
        for entry in (data.get("TopLists") or [{}])[0].get("StatList") or []:
            pid = str(entry.get("ParticiantId"))
            p = players.setdefault(pid, {
                "name": entry.get("ParticipantName"),
                "team": entry.get("TeamName"),
                "matches": entry.get("MatchesPlayed"),
            })
            p[column] = entry.get("StatValue")
            if entry.get("MatchesPlayed"):
                p["matches"] = entry.get("MatchesPlayed")
        time.sleep(REQUEST_PAUSE)

    for pid, p in players.items():
        minutes = int(p.get("minutes") or 0)
        to_total = lambda per90: (
            int(round(per90 * minutes / 90.0)) if per90 is not None and minutes else None
        )
        db.execute(
            "INSERT OR REPLACE INTO fotmob_players VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                season, LEAGUE, pid, p.get("name"), p.get("team"),
                minutes, int(p.get("matches") or 0),
                int(p.get("goals") or 0), num(p.get("xg")) or 0.0,
                int(p.get("assists") or 0), num(p.get("xa")) or 0.0,
                num(p.get("xgot")) or 0.0,
                to_total(num(p.get("shots_per90"))),
                to_total(num(p.get("sot_per90"))),
                int(p.get("chances_created") or 0),
                fetched_at,
            ),
        )
    print(f"  players: {len(players)} merged from {len(STAT_FILES)} stat files")


def match_stat_map(match_json):
    """Flatten Periods.All stat groups; keys repeat, first non-null wins."""
    out = {}
    periods = ((match_json.get("content") or {}).get("stats") or {}).get("Periods") or {}
    for group in (periods.get("All") or {}).get("stats") or []:
        for stat in group.get("stats") or []:
            key, values = stat.get("key"), stat.get("stats")
            if (key and key not in out and isinstance(values, list)
                    and len(values) == 2 and values[0] is not None):
                out[key] = values
    return out


def player_stat_map(player):
    """Flatten one player's stat groups to {label: value}."""
    out = {}
    for group in player.get("stats") or []:
        for label, item in (group.get("stats") or {}).items():
            if label in out:
                continue
            value = (item.get("stat") or {}).get("value") if isinstance(item, dict) else item
            out[label] = value
    return out


def position_of(player):
    """The player's position as text, whatever shape the feed sends it in.

    Today usualPosition is absent and positionId is a bare number; FotMob
    sends this field as an object elsewhere in the same payload, and an
    unhandled dict binds as a parameter of an unsupported type -- which is
    an exception in the middle of writing a squad.
    """
    value = player.get("usualPosition") or player.get("positionId")
    if isinstance(value, dict):
        value = value.get("label") or value.get("name") or value.get("id")
    return None if value is None else str(value)


def store_match_players(db, season, match_id, md, teams, fetched_at):
    """One row per player who was in the squad, from the same payload.

    Player ids here are the ids fotmob_players already stores, so a match
    row and a season row are the same player without any name matching.

    Rows are replaced rather than added to: a re-fetch of a match whose
    squad list has changed should not leave a player behind who is no
    longer in it. The delete and the insert are one statement pair inside
    the caller's transaction, which fetch_season commits only when the whole
    match worked, so a throw anywhere in here leaves the old squad standing.
    """
    stats = ((md.get("content") or {}).get("playerStats") or {})
    if not stats:
        return 0
    lineup = (md.get("content") or {}).get("lineup") or {}
    started = set()
    for side in ("homeTeam", "awayTeam"):
        for p in (lineup.get(side) or {}).get("starters") or []:
            started.add(str(p.get("id")))
    # who started is read from one path in the payload and nothing else can
    # stand in for it. If that path is ever renamed the set comes back empty
    # and every player in the match reads as a substitute -- a wrong squad
    # sheet, stamped current and never revisited. Refuse instead
    if not started:
        raise ValueError(f"match {match_id}: no starters in the lineup")
    names = {str(t.get("id")): t.get("name") for t in teams}

    columns = ("season, league, match_id, player_id, team, player_name, started,"
               " is_gk, shirt, position"
               + "".join(f", {column}" for column, _ in PLAYER_STATS)
               + ", fetched_at")
    placeholders = ",".join("?" * (11 + len(PLAYER_STATS)))
    rows = []
    for pid, player in stats.items():
        flat = player_stat_map(player)
        rows.append((
            season, LEAGUE, str(match_id), str(pid),
            names.get(str(player.get("teamId"))) or player.get("teamName"),
            player.get("name"),
            1 if str(pid) in started else 0,
            1 if player.get("isGoalkeeper") else 0,
            str(player.get("shirtNumber") or ""),
            position_of(player),
            *[num(flat.get(label)) for _, label in PLAYER_STATS],
            fetched_at,
        ))
    if not rows:
        return 0
    db.execute(
        "DELETE FROM fotmob_match_players WHERE season = ? AND league = ? AND match_id = ?",
        (season, LEAGUE, str(match_id)),
    )
    db.executemany(
        f"INSERT OR REPLACE INTO fotmob_match_players ({columns}) "
        f"VALUES ({placeholders})",
        rows,
    )
    return len(rows)


SHOT_COLUMNS = (
    "season, league, match_id, shot_id, team, player_id, player_name, minute,"
    " minute_added, period, x, y, xg, xgot, outcome, shot_type, situation,"
    " is_blocked, is_on_target, is_own_goal, inside_box, fetched_at"
)


def store_match_shots(db, season, match_id, md, teams, fetched_at):
    """Every shot in the match, from content.shotmap.

    FotMob has already turned the pitch round for us: both teams' shots are
    given attacking the same goal, x running 0-105 towards it and y 0-68
    across, so a shot map is drawn per team without mirroring anything.

    xG is kept at full precision -- a 0.02 chance and a 0.04 chance are a
    factor of two apart, and the circle drawn for each is sized by it. The
    report rounds; the database should not.

    Written under the caller's transaction, like the squad rows, so a match
    that fails halfway leaves the shots it already had.
    """
    shots = ((md.get("content") or {}).get("shotmap") or {}).get("shots")
    if not shots:
        return 0
    names = {str(t.get("id")): t.get("name") for t in teams}
    rows = []
    for s in shots:
        if s.get("id") is None or s.get("x") is None or s.get("y") is None:
            continue        # a shot with no location cannot be drawn
        rows.append((
            season, LEAGUE, str(match_id), str(s.get("id")),
            names.get(str(s.get("teamId"))),
            None if s.get("playerId") is None else str(s.get("playerId")),
            s.get("playerName"),
            s.get("min"), s.get("minAdded"), s.get("period"),
            num(s.get("x")), num(s.get("y")),
            num(s.get("expectedGoals")), num(s.get("expectedGoalsOnTarget")),
            s.get("eventType"), s.get("shotType"), s.get("situation"),
            1 if s.get("isBlocked") else 0,
            1 if s.get("isOnTarget") else 0,
            1 if s.get("isOwnGoal") else 0,
            1 if s.get("isFromInsideBox") else 0,
            fetched_at,
        ))
    if not rows:
        return 0
    db.execute(
        "DELETE FROM fotmob_match_shots WHERE season = ? AND league = ? AND match_id = ?",
        (season, LEAGUE, str(match_id)),
    )
    db.executemany(
        f"INSERT OR REPLACE INTO fotmob_match_shots ({SHOT_COLUMNS}) "
        f"VALUES ({','.join('?' * 22)})",
        rows,
    )
    return len(rows)


def fetch_match(db, season, match_id, fetched_at):
    md = get_json(f"{BASE}/matchDetails?matchId={match_id}")
    header_teams = (md.get("header") or {}).get("teams") or []
    if len(header_teams) != 2:
        raise ValueError(f"match {match_id}: unexpected header")
    general = md.get("general") or {}
    match_date = str(general.get("matchTimeUTCDate") or "")[:10]
    stats = match_stat_map(md)

    def side_values(key):
        values = stats.get(key)
        if not values:
            return None, None
        return num(values[0]), num(values[1])

    # A re-fetch replaces a row that is already published, so anything the
    # row is built from has to be present -- not defaulted. Refusing leaves
    # the stored match exactly as it was, which is the safe failure.
    if not match_date:
        raise ValueError(f"match {match_id}: no kickoff date in match details")
    if any(t.get("score") is None for t in header_teams):
        raise ValueError(f"match {match_id}: no score in match details")

    xg = side_values("expected_goals")
    npxg = side_values("expected_goals_non_penalty")
    xgot = side_values("expected_goals_on_target")
    shots = side_values("total_shots")
    sot = side_values("ShotsOnTarget")
    poss = side_values("BallPossesion")
    if xg[0] is None:
        raise ValueError(f"match {match_id}: no xG in match details")
    if npxg[0] is None:
        npxg = xg

    # the rest of the payload's stat groups, one value per side. A key the
    # feed did not report for this match stores NULL rather than a zero it
    # would be read as having measured
    def extra(side):
        out = []
        for _, key, kind in MATCH_STATS:
            values = stats.get(key)
            raw = values[side] if values else None
            out.append(pct(raw) if kind == "pct" else num(raw))
        return out

    for side in (0, 1):
        opp = 1 - side
        own_goals = int(header_teams[side].get("score") or 0)
        opp_goals = int(header_teams[opp].get("score") or 0)
        result = "w" if own_goals > opp_goals else "d" if own_goals == opp_goals else "l"
        db.execute(
            "INSERT OR REPLACE INTO fotmob_team_matches "
            "(season, league, match_id, team, opponent, match_date, home_away,"
            " xg, xga, npxg, npxga, xgot, xgota, shots, shots_allowed, sot,"
            " sot_allowed, possession, scored, missed, result, pts, npxgd,"
            " xpts, fetched_at, stats_version" +
            "".join(f", {column}" for column, _, _ in MATCH_STATS) +
            ") VALUES (" + ",".join("?" * (26 + len(MATCH_STATS))) + ")",
            (
                season, LEAGUE, str(match_id),
                header_teams[side].get("name"), header_teams[opp].get("name"),
                match_date, "h" if side == 0 else "a",
                xg[side], xg[opp], npxg[side], npxg[opp],
                xgot[side] if xgot[0] is not None else None,
                xgot[opp] if xgot[0] is not None else None,
                int(shots[side]) if shots[0] is not None else None,
                int(shots[opp]) if shots[0] is not None else None,
                int(sot[side]) if sot[0] is not None else None,
                int(sot[opp]) if sot[0] is not None else None,
                poss[side] if poss[0] is not None else None,
                own_goals, opp_goals, result,
                3 if result == "w" else 1 if result == "d" else 0,
                round(npxg[side] - npxg[opp], 2),
                poisson_xpts(xg[side], xg[opp]),
                fetched_at, STATS_VERSION,
                *extra(side),
            ),
        )

    store_match_players(db, season, match_id, md, header_teams, fetched_at)
    store_match_shots(db, season, match_id, md, header_teams, fetched_at)


def fetch_season(db, season, fetched_at, refresh_budget=REFRESH_PER_RUN):
    print(f"--- {LEAGUE} {season} ---")
    league_json = get_json(f"{BASE}/leagues?id={LEAGUE_ID}&season={season}")
    (DATA_DIR / f"fotmob_allsvenskan_{season}.json").write_text(
        json.dumps(league_json, indent=1), encoding="utf-8"
    )

    stat_id = season_stat_id(league_json, season)
    if stat_id:
        fetch_players(db, season, stat_id, fetched_at)
        db.commit()
    else:
        print(f"  ! no player stat link for {season} - match data only")

    fixtures = (league_json.get("fixtures") or {}).get("allMatches") or []
    finished = sorted(
        (m for m in fixtures if (m.get("status") or {}).get("finished")),
        key=lambda m: str((m.get("status") or {}).get("utcTime") or ""),
    )
    stored = {r[0] for r in db.execute(
        "SELECT DISTINCT match_id FROM fotmob_team_matches WHERE season = ? AND league = ?",
        (season, LEAGUE),
    )}
    # a match stored before the current stat set existed is worth revisiting:
    # it is the same one request, and it is the only way the new columns ever
    # get filled for a match already on file
    stale = {r[0] for r in db.execute(
        "SELECT DISTINCT match_id FROM fotmob_team_matches "
        "WHERE season = ? AND league = ? "
        "AND (stats_version IS NULL OR stats_version < ?)",
        (season, LEAGUE, STATS_VERSION),
    )}
    new = [m for m in finished if str(m.get("id")) not in stored]
    # newest first, because the site only shows the last ten results and a
    # part-done refresh should have covered every match anyone can see
    refresh = [m for m in reversed(finished)
               if str(m.get("id")) in stale][:max(0, refresh_budget)]
    todo = new + refresh
    print(f"  matches: {len(finished)} finished, {len(stored)} stored, "
          f"{len(new)} new, {len(refresh)} of {len(stale)} to re-fetch for stats "
          f"v{STATS_VERSION}")
    misses = 0
    for i, m in enumerate(todo, 1):
        try:
            fetch_match(db, season, m["id"], fetched_at)
            db.commit()
            misses = 0
        except Exception as error:
            # the commit used to sit below this block, so a match that threw
            # halfway was written anyway: team rows stamped with the current
            # stats_version -- which takes the match out of the re-fetch set
            # for good -- and a squad deleted but not yet replaced. Rolling
            # back leaves the stored match exactly as it was, which is the
            # failure this fetcher is meant to have
            db.rollback()
            misses += 1
            print(f"  ! match {m.get('id')} skipped: {error}")
            # one bad match is a bad match; eight in a row is the feed having
            # changed shape, and this script promises to fail loudly at that
            # rather than let --strict publish on a quietly frozen dataset
            if misses >= FAIL_STREAK:
                raise RuntimeError(
                    f"{misses} consecutive match fetches failed - "
                    f"matchDetails has probably changed shape") from error
        if i % 25 == 0:
            print(f"  ... {i}/{len(todo)}")
        time.sleep(REQUEST_PAUSE)
    return len(refresh)


def seasons_from_args(argv):
    if "--backfill" in argv:
        return [str(y) for y in range(FIRST_SEASON, int(SEASON) + 1)]
    explicit = [a for a in argv if not a.startswith("-")]
    if explicit:
        bad = [a for a in explicit if not a.isdigit() or not FIRST_SEASON <= int(a) <= int(SEASON)]
        if bad:
            raise SystemExit(f"Season must be a year {FIRST_SEASON}-{SEASON}, got: {' '.join(bad)}")
        return explicit
    return [SEASON]


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    migrate(db)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # the refresh budget is for the run, not for each season: newest season
    # first, and whatever is left over goes to the older ones
    budget = 10 ** 6 if "--refresh-all" in sys.argv[1:] else REFRESH_PER_RUN
    for season in sorted(seasons_from_args(sys.argv[1:]), reverse=True):
        budget -= fetch_season(db, season, fetched_at, refresh_budget=budget) or 0

    print(f"\nDatabase: {DB_PATH.name}")
    for row in db.execute(
        "SELECT season, COUNT(DISTINCT match_id), "
        "(SELECT COUNT(*) FROM fotmob_players p WHERE p.season = m.season), "
        "(SELECT COUNT(*) FROM fotmob_match_players q WHERE q.season = m.season) "
        "FROM fotmob_team_matches m GROUP BY season ORDER BY season"
    ):
        print(f"  {row[0]}: {row[1]} matches, {row[2]} players, "
              f"{row[3]} player-match lines")
    db.close()


if __name__ == "__main__":
    main()
