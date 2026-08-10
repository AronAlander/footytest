"""Reconstruct history for projection_log.py's season-over-time chart.

The chart needs one snapshot per night the projection ran, but logging
only started the night this feature shipped — so a season already in
progress (right now, Allsvenskan) would sit with an empty chart until the
season is nearly over. This walks back through the matches already played
and computes what the nightly build WOULD have logged on each one, using
_compute_projection's as_of parameter, which is built exactly for this: it
excludes everything on or after as_of, including matches that have since
been played for real, so a backfilled snapshot cannot see its own future.

One snapshot per distinct date something was played, dated the day of that
last match played before the cut (not the cut date itself) so points on
the chart line up with actual matchdays. Fewer simulations than the live
build (PROJECT_BACKFILL_SIMS vs PROJECT_SIMS) because this computes dozens
of snapshots in one run rather than one a night; the sampling error this
trades away is invisible next to a 1-point-wide chart axis.

Idempotent: reruns simply overwrite the same dated rows. Safe to run again
after a fetch fills in results the fetcher missed the first time.

Usage:
    python backfill_projection_log.py [league ...]   # default: all leagues
                                                       # with an in-progress
                                                       # season and 2+ dates
                                                       # of results already
"""
import sqlite3
import sys
from datetime import datetime, timedelta

import build_report as br
import projection_log

PROJECT_BACKFILL_SIMS = 1500


def played_dates(db, league, season):
    return [r[0] for r in db.execute(
        """SELECT DISTINCT match_date FROM matches
           WHERE league = ? AND season = ? AND home_score IS NOT NULL
           ORDER BY match_date""",
        (league, season),
    )]


def backfill_league(db, league, logged):
    found = br._projection_fixtures(db, league)
    if not found:
        print(f"  {league}: no in-progress season, skipping")
        return 0
    season, rows = found
    dates = played_dates(db, league, season)
    if len(dates) < 2:
        print(f"  {league} {season}: only {len(dates)} matchday(s) played, "
              "not enough to backfill")
        return 0

    written = 0
    for d in dates:
        as_of = (datetime.strptime(d[:10], "%Y-%m-%d").date()
                 + timedelta(days=1))
        r = br._compute_projection(db, league, as_of=as_of, season=season,
                                   sims=PROJECT_BACKFILL_SIMS)
        if not r:
            continue
        projection_log.record_snapshot(logged, d[:10], league, season,
                                       r["teams"], r["proj"], r["title"],
                                       r["europe"], r["drop"], r["sims"])
        written += 1
    print(f"  {league} {season}: {written} snapshots from {len(dates)} matchdays")
    return written


def main():
    db = sqlite3.connect(br.DB_PATH)
    leagues = sys.argv[1:] or br.LEAGUE_ORDER
    logged = projection_log.load()
    total = sum(backfill_league(db, lg, logged) for lg in leagues)
    if total:
        projection_log.save(logged)
        print(f"\n{total} snapshot-rows written to {projection_log.LOG_PATH}")
    else:
        print("\nnothing to backfill")


if __name__ == "__main__":
    main()
