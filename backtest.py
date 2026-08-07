"""Backtest the xG Poisson prediction model against played history.

Replays every league season in the database: each match is predicted using
only team-match rows dated BEFORE it (no leakage), exactly as
build_report.py's predictions block would have done on the morning of the
game, and the predictions are scored against what actually happened.

The team-strength definition is raced across variants (all using the
production model's 400-day cross-season lookback and recency half-life):

  xg            attack/defence = weighted mean xG for/against
  npxg          non-penalty xG — penalties are mostly noise
  xg+goals      0.7*xG + 0.3*actual goals — lets persistent finishing
                skill into the strengths
  npxg+goals    the blend on a non-penalty base — the best scorer, now
                what production ships (the 0.3 goals weight is the
                optimum of a 0.1-0.5 sweep)

plus two baselines: uniform (1/3 each) and each league's overall
home/draw/away base rates.

Scores: multiclass Brier (lower is better; uniform scores 0.667),
log-loss (lower is better), accuracy of the most likely outcome.

An earlier version of this script also raced the lookback window itself:
the 400-day cross-season window beat same-season-only on every metric
(and predicts early-season rounds), which is why production uses it.

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
    PREDICT_LOOKBACK_DAYS,
    _outcome_probs,
)

MIN_PRIOR_MATCHES = 6           # both teams need this much history
XG_MIRROR_TOLERANCE = 0.005     # pairing home/away rows via mirrored xG
GOALS_BLEND = 0.3               # weight of actual goals in the blend variants

VARIANTS = {
    "xg": (
        lambda r: r["xg"],
        lambda r: r["xga"],
    ),
    "npxg": (
        lambda r: r["npxg"],
        lambda r: r["npxga"],
    ),
    "xg+goals": (
        lambda r: (1 - GOALS_BLEND) * r["xg"] + GOALS_BLEND * r["scored"],
        lambda r: (1 - GOALS_BLEND) * r["xga"] + GOALS_BLEND * r["missed"],
    ),
    "npxg+goals": (
        lambda r: (1 - GOALS_BLEND) * r["npxg"] + GOALS_BLEND * r["scored"],
        lambda r: (1 - GOALS_BLEND) * r["npxga"] + GOALS_BLEND * r["missed"],
    ),
}


def load_team_rows(db):
    """All per-team match rows (both sources), every season. npxG falls back
    to xG where a source does not provide it."""
    sql = """SELECT season, league, team, match_date, home_away, xg, xga,
                    scored, missed, npxg, npxga
             FROM {table}
             WHERE xg IS NOT NULL AND xga IS NOT NULL AND match_date IS NOT NULL"""
    raw = db.execute(sql.format(table="understat_team_matches")).fetchall()
    if db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='fotmob_team_matches'"
    ).fetchone():
        raw += db.execute(sql.format(table="fotmob_team_matches")).fetchall()
    rows = []
    for season, league, team, day, ha, xg, xga, scored, missed, npxg, npxga in raw:
        day = day[:10]
        rows.append({
            "season": season, "league": league, "team": team,
            "day": day, "ord": _ordinal(day), "ha": ha, "xg": xg, "xga": xga,
            "scored": scored, "missed": missed,
            "npxg": npxg if npxg is not None else xg,
            "npxga": npxga if npxga is not None else xga,
        })
    return rows


def pair_matches(team_rows):
    """Home and away rows describe the same game with mirrored score and xG;
    join them on that mirror. Ambiguous pairs (same league, day, score AND
    xG twice over) are dropped — they are practically nonexistent."""
    by_day = defaultdict(lambda: ([], []))
    for r in team_rows:
        by_day[(r["league"], r["day"])][0 if r["ha"] == "h" else 1].append(r)
    matches = []
    for (league, day), (homes, aways) in by_day.items():
        for h in homes:
            candidates = [
                a for a in aways
                if a["scored"] == h["missed"] and a["missed"] == h["scored"]
                and abs(a["xg"] - h["xga"]) < XG_MIRROR_TOLERANCE
                and abs(a["xga"] - h["xg"]) < XG_MIRROR_TOLERANCE
            ]
            if len(candidates) == 1:
                a = candidates[0]
                matches.append({
                    "league": league, "season": h["season"], "date": day,
                    "home": h["team"], "away": a["team"],
                    "home_goals": h["scored"], "away_goals": h["missed"],
                })
    matches.sort(key=lambda m: (m["league"], m["date"], m["home"]))
    return matches


def index_histories(team_rows):
    hist = defaultdict(list)
    for r in team_rows:
        hist[(r["league"], r["team"])].append(r)
    for rows in hist.values():
        rows.sort(key=lambda r: r["ord"])
    return hist


def league_rows(team_rows):
    per_league = defaultdict(list)
    for r in team_rows:
        per_league[r["league"]].append(r)
    for rows in per_league.values():
        rows.sort(key=lambda r: r["ord"])
    return per_league


_ORDINAL_CACHE = {}


def _ordinal(day):
    """date string -> proleptic ordinal int, cached (strptime is slow)."""
    if day not in _ORDINAL_CACHE:
        _ORDINAL_CACHE[day] = datetime.strptime(day, "%Y-%m-%d").date().toordinal()
    return _ORDINAL_CACHE[day]


def weighted_strength(history, as_of_ord, att, dfn):
    """(attack, defence) from rows strictly inside the lookback window, or
    None with fewer than MIN_PRIOR_MATCHES of them."""
    att_sum = def_sum = w_sum = 0.0
    n = 0
    for r in history:
        if r["ord"] >= as_of_ord:
            break
        age = as_of_ord - r["ord"]
        if age > PREDICT_LOOKBACK_DAYS:
            continue
        w = 0.5 ** (age / PREDICT_HALF_LIFE_DAYS)
        att_sum += w * att(r)
        def_sum += w * dfn(r)
        w_sum += w
        n += 1
    if n < MIN_PRIOR_MATCHES or w_sum <= 0:
        return None
    return att_sum / w_sum, def_sum / w_sum


def league_context(rows, league, as_of_ord, variant, att, cache={}):
    """(mu, home_adv) over league rows strictly before as_of."""
    key = (league, as_of_ord, variant)
    if key in cache:
        return cache[key]
    sums = {"h": [0.0, 0.0], "a": [0.0, 0.0]}
    for r in rows:
        if r["ord"] >= as_of_ord:
            break
        age = as_of_ord - r["ord"]
        if age > PREDICT_LOOKBACK_DAYS:
            continue
        w = 0.5 ** (age / PREDICT_HALF_LIFE_DAYS)
        if r["ha"] in sums:
            sums[r["ha"]][0] += w * att(r)
            sums[r["ha"]][1] += w
    total_v = sums["h"][0] + sums["a"][0]
    total_w = sums["h"][1] + sums["a"][1]
    mu = total_v / total_w if total_w else 0.0
    home_adv = 1.0
    if sums["h"][1] and sums["a"][1] and sums["a"][0]:
        home_adv = (sums["h"][0] / sums["h"][1]) / (sums["a"][0] / sums["a"][1])
    cache[key] = (mu, home_adv)
    return mu, home_adv


def predict(match, hist, per_league, variant):
    att, dfn = VARIANTS[variant]
    as_of_ord, league = _ordinal(match["date"]), match["league"]
    home = weighted_strength(hist[(league, match["home"])], as_of_ord, att, dfn)
    away = weighted_strength(hist[(league, match["away"])], as_of_ord, att, dfn)
    if not home or not away:
        return None
    mu, home_adv = league_context(per_league[league], league, as_of_ord, variant, att)
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
    per_league = league_rows(team_rows)
    print(f"{len(team_rows)} team-match rows -> {len(matches)} paired matches\n")

    league_rates = defaultdict(lambda: [0, 0, 0])
    for m in matches:
        outcome = 0 if m["home_goals"] > m["away_goals"] else (1 if m["home_goals"] == m["away_goals"] else 2)
        league_rates[m["league"]][outcome] += 1

    names = list(VARIANTS) + ["base rates", "uniform"]
    scorers = {name: Scorer() for name in names}
    for m in matches:
        outcome = 0 if m["home_goals"] > m["away_goals"] else (1 if m["home_goals"] == m["away_goals"] else 2)
        preds = {v: predict(m, hist, per_league, v) for v in VARIANTS}
        if not all(preds.values()):
            continue  # same match set for every variant
        counts = league_rates[m["league"]]
        total = sum(counts)
        for v, p in preds.items():
            scorers[v].add(p, outcome)
        scorers["base rates"].add([c / total for c in counts], outcome)
        scorers["uniform"].add([1 / 3] * 3, outcome)

    print("All variants use the production lookback "
          f"({PREDICT_LOOKBACK_DAYS}-day cross-season window):")
    for name in names:
        print(f"  {name:12s}{scorers[name].row()}")

    print("\nLeague base rates (home/draw/away):")
    for league, counts in sorted(league_rates.items()):
        total = sum(counts)
        print(f"  {league:16s} " + " / ".join(f"{c / total * 100:.0f}%" for c in counts)
              + f"   ({total} matches)")


if __name__ == "__main__":
    main()
