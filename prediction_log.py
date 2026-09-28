"""A durable record of what the model predicted, before it knew the result.

The backtest replays history with today's code, which is the right way to
choose a model but the wrong way to be held to account: every coefficient
in it was chosen by looking at those same matches, and a rewrite tomorrow
silently rewrites the past. This file exists so the site can be graded on
calls it actually published — written down while the fixture was still
unplayed, and never edited afterwards.

Storage is a committed CSV, not the database. football.sqlite is
gitignored and lives in the Actions cache, which has already been lost
once; a prediction record that can evaporate proves nothing. The CSV is
small (one row per fixture, a couple of thousand a season), diffable, and
survives a cold cache.

One row per fixture, keyed by TheSportsDB's event id, which is stable
across postponements:

  first_seen / p*_first   the earliest call, from up to two weeks out
  last_seen / p*          the freshest call before kickoff, updated on
                          every build while the fixture is unplayed and
                          frozen at the recorded UTC kickoff

Grading uses the frozen last call — the model's best information at
kickoff. Keeping the first one too costs three columns and answers a
question worth asking later: does the model actually improve as a match
approaches, or is it just as good a fortnight out?
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "predictions" / "log.csv"

FIELDS = [
    "event_id", "league", "season", "match_date", "home", "away",
    "first_seen", "p_home_first", "p_draw_first", "p_away_first",
    "last_seen", "p_home", "p_draw", "p_away", "lam_home", "lam_away",
    "kickoff_utc", "first_recorded_at", "last_recorded_at",
    "first_model_version", "model_version",
]


def load(path=LOG_PATH):
    """event_id -> row dict. Missing file is not an error: the log starts
    empty on a fresh clone and fills up as fixtures are predicted."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {row["event_id"]: row for row in csv.DictReader(fh)}


def save(rows, path=LOG_PATH):
    path.parent.mkdir(exist_ok=True)
    ordered = sorted(rows.values(), key=lambda r: (r["match_date"], r["league"],
                                                   r["home"], r["event_id"]))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(ordered)


def kickoff_utc(match_date, match_time):
    """TheSportsDB's UTC date/time; unknown times freeze at start of the day.

    An unknown kickoff must never permit a same-day revision after play began.
    """
    try:
        text = f"{match_date[:10]}T{match_time or '00:00:00'}"
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def record(rows, today, event_id, league, season, match_date, home, away,
           probs, lambdas, *, match_time=None, now=None, model_version="unknown"):
    """Record only before kickoff, even if the result feed is delayed.

    Legacy rows keep unknown provenance blank. An already reached stored
    kickoff stays frozen even if a later payload moves the fixture date.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("recording time must be timezone-aware")
    cutoff = kickoff_utc(match_date, match_time)
    event_id = str(event_id)
    existing = rows.get(event_id)
    previous_cutoff = (datetime.fromisoformat(existing["kickoff_utc"])
                       if existing and existing.get("kickoff_utc") else None)
    if cutoff is None or now >= cutoff or (previous_cutoff and now >= previous_cutoff):
        return False
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    p_home, p_draw, p_away = (f"{p:.4f}" for p in probs)
    values = {
        "match_date": match_date, "last_seen": stamp[:10],
        "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        "lam_home": f"{lambdas[0]:.3f}", "lam_away": f"{lambdas[1]:.3f}",
        "kickoff_utc": cutoff.isoformat(), "last_recorded_at": stamp,
        "model_version": model_version,
    }
    if existing:
        existing.update(values)
    else:
        rows[event_id] = {
            "event_id": event_id, "league": league, "season": season,
            "home": home, "away": away, "first_seen": stamp[:10],
            "p_home_first": p_home, "p_draw_first": p_draw, "p_away_first": p_away,
            "first_recorded_at": stamp, "first_model_version": model_version,
            **values,
        }
    return True


def graded(db, rows, league=None):
    """Logged predictions whose match has since finished, newest first.

    Yields (row, home_score, away_score, outcome) with outcome 0/1/2 for
    home/draw/away. Results are read live from the matches table rather
    than copied into the CSV, so a corrected score corrects the grade.
    """
    if not rows:
        return []
    results = {
        str(event_id): (h, a)
        for event_id, h, a in db.execute(
            "SELECT event_id, home_score, away_score FROM main.matches "
            "WHERE home_score IS NOT NULL AND away_score IS NOT NULL"
        )
    }
    out = []
    for row in rows.values():
        if league and row["league"] != league:
            continue
        score = results.get(row["event_id"])
        if not score:
            continue
        home_score, away_score = score
        outcome = (0 if home_score > away_score
                   else 1 if home_score == away_score else 2)
        out.append((row, home_score, away_score, outcome))
    out.sort(key=lambda item: (item[0]["match_date"], item[0]["home"]),
             reverse=True)
    return out


def probabilities(row, first=False):
    keys = (("p_home_first", "p_draw_first", "p_away_first") if first
            else ("p_home", "p_draw", "p_away"))
    return [float(row[key]) for key in keys]
