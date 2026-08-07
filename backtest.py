"""Backtest the xG Poisson prediction model against played history.

Replays every league season in the database: each match is predicted using
only team-match xG rows dated BEFORE it (no leakage), exactly as
build_report.py's predictions block would have done on the morning of the
game. Predictions are scored against what actually happened.

Two model variants are compared:

  same-season   strengths from the current season's prior matches only —
                what the dashboard does today
  cross-season  strengths from all prior matches within a 400-day window,
                so early-season predictions lean on last season's form

and two baselines:

  uniform       1/3 home, 1/3 draw, 1/3 away
  base rates    each league's overall home/draw/away frequencies

Scores: multiclass Brier (lower is better; uniform scores 0.667),
log-loss (lower is better), and plain accuracy of the most likely outcome.

Usage:
    python backtest.py
"""

import math
import sqlite3
from collections import defaultdict
from datetime import datetime

from build_report import (
    DB_PATH,
    PREDICT_HALF_LIFE_DAYS,
    _outcome_probs,
)

CROSS_SEASON_WINDOW_DAYS = 400  # cross-season variant's lookback
MIN_PRIOR_MATCHES = 6           # both teams need this much history
XG_MIRROR_TOLERANCE = 0.005     # pairing home/away rows via mirrored xG


def load_team_rows(db):
    """All per-team match rows (both sources), every season."""
    rows = db.execute(
        """SELECT season, league, team, match_date, home_away, xg, xga,
                  scored, missed
           FROM understat_team_matches
           WHERE xg IS NOT NULL AND xga IS NOT NULL AND match_date IS NOT NULL"""
    ).fetchall()
    if db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='fotmob_team_matches'"
    ).fetchone():
        rows += db.execute(
            """SELECT season, league, team, match_date, home_away, xg, xga,
                      scored, missed
               FROM fotmob_team_matches
               WHERE xg IS NOT NULL AND xga IS NOT NULL AND match_date IS NOT NULL"""
        ).fetchall()
    return [
        (season, league, team, match_date[:10], home_away, xg, xga, scored, missed)
        for season, league, team, match_date, home_away, xg, xga, scored, missed in rows
    ]


def pair_matches(team_rows):
    """Home and away rows describe the same game with mirrored score and xG;
    join them on that mirror. Ambiguous pairs (same league, day, score AND
    xG twice over) are dropped — they are practically nonexistent."""
    by_day = defaultdict(lambda: ([], []))
    for row in team_rows:
        season, league, team, day, home_away, xg, xga, scored, missed = row
        by_day[(league, day)][0 if home_away == "h" else 1].append(row)
    matches = []
    for (league, day), (homes, aways) in by_day.items():
        for h in homes:
            candidates = [
                a for a in aways
                if a[7] == h[8] and a[8] == h[7]
                and abs(a[5] - h[6]) < XG_MIRROR_TOLERANCE
                and abs(a[6] - h[5]) < XG_MIRROR_TOLERANCE
            ]
            if len(candidates) == 1:
                a = candidates[0]
                matches.append({
                    "league": league, "season": h[0], "date": day,
                    "home": h[2], "away": a[2],
                    "home_goals": h[7], "away_goals": h[8],
                })
    matches.sort(key=lambda m: (m["league"], m["date"], m["home"]))
    return matches


def index_histories(team_rows):
    """(league, team) -> chronologically sorted [(date, xg, xga, home_away, season)]."""
    hist = defaultdict(list)
    for season, league, team, day, home_away, xg, xga, _, _ in team_rows:
        hist[(league, team)].append((day, xg, xga, home_away, season))
    for rows in hist.values():
        rows.sort()
    return hist


def league_days(team_rows):
    """(league) -> sorted per-day league rows for rolling mu / home-advantage."""
    per_league = defaultdict(list)
    for season, league, _, day, home_away, xg, _, _, _ in team_rows:
        per_league[league].append((day, home_away, xg, season))
    for rows in per_league.values():
        rows.sort()
    return per_league


def _days_between(later, earlier):
    return (datetime.strptime(later, "%Y-%m-%d") - datetime.strptime(earlier, "%Y-%m-%d")).days


def weighted_strength(history, as_of, season, cross_season):
    """(attack, defence, n) from rows strictly before as_of, or None."""
    xg_sum = xga_sum = w_sum = 0.0
    n = 0
    for day, xg, xga, _, row_season in history:
        if day >= as_of:
            break
        if cross_season:
            age = _days_between(as_of, day)
            if age > CROSS_SEASON_WINDOW_DAYS:
                continue
        else:
            if row_season != season:
                continue
            age = _days_between(as_of, day)
        w = 0.5 ** (age / PREDICT_HALF_LIFE_DAYS)
        xg_sum += w * xg
        xga_sum += w * xga
        w_sum += w
        n += 1
    if n < MIN_PRIOR_MATCHES or w_sum <= 0:
        return None
    return xg_sum / w_sum, xga_sum / w_sum, n


def league_context(rows, as_of, season, cross_season, cache={}):
    """(mu, home_adv) from league rows strictly before as_of."""
    key = (id(rows), as_of, season, cross_season)
    if key in cache:
        return cache[key]
    sums = {"h": [0.0, 0.0], "a": [0.0, 0.0]}
    for day, home_away, xg, row_season in rows:
        if day >= as_of:
            break
        if cross_season:
            age = _days_between(as_of, day)
            if age > CROSS_SEASON_WINDOW_DAYS:
                continue
        else:
            if row_season != season:
                continue
            age = _days_between(as_of, day)
        w = 0.5 ** (age / PREDICT_HALF_LIFE_DAYS)
        if home_away in sums:
            sums[home_away][0] += w * xg
            sums[home_away][1] += w
    total_xg = sums["h"][0] + sums["a"][0]
    total_w = sums["h"][1] + sums["a"][1]
    mu = total_xg / total_w if total_w else 0.0
    home_adv = 1.0
    if sums["h"][1] and sums["a"][1] and sums["a"][0]:
        home_adv = (sums["h"][0] / sums["h"][1]) / (sums["a"][0] / sums["a"][1])
    cache[key] = (mu, home_adv)
    return mu, home_adv


def predict(match, hist, lg_rows, cross_season):
    as_of, season, league = match["date"], match["season"], match["league"]
    home = weighted_strength(hist[(league, match["home"])], as_of, season, cross_season)
    away = weighted_strength(hist[(league, match["away"])], as_of, season, cross_season)
    if not home or not away:
        return None
    mu, home_adv = league_context(lg_rows[league], as_of, season, cross_season)
    if mu <= 0:
        return None
    sqrt_ha = math.sqrt(home_adv)
    lam_home = max(0.1, min(6.0, home[0] * away[1] / mu * sqrt_ha))
    lam_away = max(0.1, min(6.0, away[0] * home[1] / mu / sqrt_ha))
    p_home, p_draw, p_away, _ = _outcome_probs(lam_home, lam_away)
    return p_home, p_draw, p_away


class Scorer:
    def __init__(self):
        self.n = 0
        self.brier = 0.0
        self.logloss = 0.0
        self.hits = 0

    def add(self, probs, outcome):  # outcome: 0 home, 1 draw, 2 away
        self.n += 1
        target = [0.0, 0.0, 0.0]
        target[outcome] = 1.0
        self.brier += sum((p - t) ** 2 for p, t in zip(probs, target))
        self.logloss += -math.log(max(probs[outcome], 1e-9))
        self.hits += int(max(range(3), key=lambda i: probs[i]) == outcome)

    def row(self):
        if not self.n:
            return "        (no predictions)"
        return (f"  n={self.n:5d}  Brier {self.brier / self.n:.4f}"
                f"  log-loss {self.logloss / self.n:.4f}"
                f"  accuracy {self.hits / self.n * 100:.1f}%")


def main():
    db = sqlite3.connect(DB_PATH)
    team_rows = load_team_rows(db)
    matches = pair_matches(team_rows)
    hist = index_histories(team_rows)
    lg_rows = league_days(team_rows)
    print(f"{len(team_rows)} team-match rows -> {len(matches)} paired matches\n")

    league_rates = defaultdict(lambda: [0, 0, 0])
    for m in matches:
        outcome = 0 if m["home_goals"] > m["away_goals"] else (1 if m["home_goals"] == m["away_goals"] else 2)
        league_rates[m["league"]][outcome] += 1

    # Set A: both variants can predict (mid-season). Set B: only cross-season
    # can (early rounds). Each variant/baseline is scored per set.
    scorers = defaultdict(Scorer)
    for m in matches:
        outcome = 0 if m["home_goals"] > m["away_goals"] else (1 if m["home_goals"] == m["away_goals"] else 2)
        p_same = predict(m, hist, lg_rows, cross_season=False)
        p_cross = predict(m, hist, lg_rows, cross_season=True)
        counts = league_rates[m["league"]]
        total = sum(counts)
        base = [c / total for c in counts]
        if p_same and p_cross:
            scorers[("A", "same-season")].add(p_same, outcome)
            scorers[("A", "cross-season")].add(p_cross, outcome)
            scorers[("A", "base rates")].add(base, outcome)
            scorers[("A", "uniform")].add([1 / 3] * 3, outcome)
        elif p_cross:
            scorers[("B", "cross-season")].add(p_cross, outcome)
            scorers[("B", "base rates")].add(base, outcome)
            scorers[("B", "uniform")].add([1 / 3] * 3, outcome)

    print("SET A - mid-season matches (both teams have 6+ matches this season):")
    for name in ("same-season", "cross-season", "base rates", "uniform"):
        print(f"  {name:14s}{scorers[('A', name)].row()}")
    print("\nSET B - early-season matches (same-season model cannot predict):")
    for name in ("cross-season", "base rates", "uniform"):
        print(f"  {name:14s}{scorers[('B', name)].row()}")

    print("\nLeague base rates (home/draw/away):")
    for league, counts in sorted(league_rates.items()):
        total = sum(counts)
        print(f"  {league:16s} " + " / ".join(f"{c / total * 100:.0f}%" for c in counts)
              + f"   ({total} matches)")


if __name__ == "__main__":
    main()
