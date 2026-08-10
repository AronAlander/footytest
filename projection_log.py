"""A durable record of what the season projection said, night by night.

season_projection_block recomputes the whole season from scratch on every
build — that is the point of it, a club's projected finish should move as
its form does. But that same freshness means the live page only ever shows
today's opinion; there is no way to see Sirius's title chances climb from
40% in April to 94% now without somewhere keeping yesterday's numbers too.

Storage follows prediction_log.py's reasoning exactly: a committed CSV, not
a database table, because football.sqlite is gitignored and lives in the
Actions cache, which has already been lost once. One row per (date, league,
team) — a season has ~20 teams and a few dozen build days, so this stays a
few thousand rows even by May, comfortably diffable.

Unlike a match prediction, a projection snapshot never freezes: the row for
today simply gets overwritten if the build runs again the same day. There
is nothing here to protect from being edited "after the fact" — the whole
series is supposed to move.
"""

import csv
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "predictions" / "projection_log.csv"

FIELDS = [
    "date", "league", "season", "team",
    "proj_pts", "title_pct", "top4_pct", "bottom3_pct",
]


def _key(date, league, team):
    return f"{date}|{league}|{team}"


def load(path=LOG_PATH):
    """(date, league, team) key -> row dict. Missing file is not an error."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {_key(r["date"], r["league"], r["team"]): r
                for r in csv.DictReader(fh)}


def save(rows, path=LOG_PATH):
    path.parent.mkdir(exist_ok=True)
    ordered = sorted(rows.values(),
                     key=lambda r: (r["league"], r["season"], r["team"], r["date"]))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(ordered)


def record_snapshot(rows, today, league, season, teams, proj, title, europe,
                    drop, sims):
    """Overwrite today's row for every team in this league's projection.

    teams/proj/title/europe/drop are the parallel lists season_projection_
    block already built for the live table — passed straight through rather
    than recomputed, so the logged numbers are exactly what the page showed.
    """
    for i, team in enumerate(teams):
        rows[_key(today, league, team)] = {
            "date": today, "league": league, "season": season, "team": team,
            "proj_pts": f"{proj[i]:.1f}",
            "title_pct": f"{title[i] / sims:.4f}",
            "top4_pct": f"{europe[i] / sims:.4f}",
            "bottom3_pct": f"{drop[i] / sims:.4f}",
        }


def series(rows, league, season=None):
    """{team: [(date, proj_pts, title_pct, top4_pct, bottom3_pct), ...]},
    each list sorted chronologically. season=None takes whatever season(s)
    are logged for the league (in practice always one at a time, since a
    league only has an in-progress season to log)."""
    out = {}
    for r in rows.values():
        if r["league"] != league or (season is not None and r["season"] != season):
            continue
        out.setdefault(r["team"], []).append((
            r["date"], float(r["proj_pts"]), float(r["title_pct"]),
            float(r["top4_pct"]), float(r["bottom3_pct"]),
        ))
    for pts in out.values():
        pts.sort(key=lambda p: p[0])
    return out
