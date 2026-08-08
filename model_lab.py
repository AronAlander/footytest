"""Race alternative prediction models against the one the site ships.

backtest.py is the referee for the *strength definition* (which xG flavour
feeds the model). This is the bench for the *model family* itself: same
data, same no-leakage replay, different machinery turning history into
win/draw/win probabilities.

Every model is scored on two eras — trained/tuned on matches before
2021-07-01, verified on 2021-07-01 onwards, which never influences a
single coefficient. A model only deserves shipping if it wins on the test
era, not just the train era.

Models
  production    what the site ships today: recency-weighted npxG+goals
                attack/defence means, opponent-adjusted by dividing
                through the league mean, home advantage from the league's
                own home/away split, deep-completions territory term,
                independent Poisson
  dixon-coles   production's expected goals, plus the Dixon & Coles (1997)
                low-score correction that fattens 0-0/1-1/1-0/0-1 —
                the classic fix for Poisson under-predicting draws
  fitted        replaces the mean-ratio heuristic with a proper weighted
                Poisson fit: attack and defence solved jointly by
                iterative scaling so a club's numbers are adjusted for
                exactly who it played, not just the league average
  elo           a different family entirely: one rating per club updated
                after every match, mapped to three outcomes by an ordered
                logistic. Knows only results.
  elo-xg        the same, updated on the xG margin instead of the result
  blend         production and the better Elo averaged
  shrunk        production's probabilities pulled toward the league's base
                rates (a pure calibration layer, no new information)
  temperature   production's probabilities sharpened/flattened by a power

Findings, 2026-08-08 (20,500 matches; 11,913 train / 8,587 held out).
Test-era Brier, and the paired t of the per-match difference against
production — same fixtures, same outcomes, so |t| under about 2 is noise:

  production    0.5874   —
  dixon-coles   0.5874   rho optimum is 0: the correction is for models
                         fitted to goals, and ours is already built on xG
                         means, so it has no draw deficit to repair
  fitted        0.5877   t=+0.5. Solving attack/defence jointly is the
                         textbook upgrade and it changes nothing: with 20
                         clubs the schedule is nearly balanced inside a
                         400-day window, so the opponent adjustment the
                         fit performs is one production already gets by
                         dividing through the league mean — and the fit
                         pays for it in estimation noise
  supremacy     0.5888   t=+2.4 WORSE. Poisson's link is doing real work:
                         collapsing the two expected-goal numbers into
                         one supremacy figure throws away that 2.1-1.9
                         and 0.6-0.4 are different matches
  h2h-outcome   0.5869   t=-3.0. Nudging the model 5% toward the pair's
                         own historical result split genuinely helps
  h2h-surprise  0.5873   t=-1.3 NOTHING, and this is the interesting one.
                         It feeds back only the part of the pair's history
                         the model failed to predict — and that part
                         carries no signal. The xG-residual form agrees
                         (t=-1.6). So there is no bogey-team effect here:
                         the raw record works because it re-states how far
                         apart the two clubs are, evidence a 400-day
                         window under-weights, not because of anything
                         chemical between them. The tuned config says the
                         same thing — flat weighting, so a meeting from
                         2015 counts as much as one from last season,
                         which is not how a rivalry effect would behave
  elo           0.5975   t=+6.3 worse — results-only ratings are simply
                         a weaker signal than chance quality
  elo-xg        0.5921   t=+3.3 worse, but much closer: the xG margin
                         recovers most of the gap
  blend/elo-xg  0.5864   t=-3.7 — the ONLY real improvement. 80%
                         production + 20% Elo-on-xG. Elo is worse alone
                         yet carries something production lacks (a club's
                         standing propagates through who beat whom),
                         so averaging helps. Tiny: -0.0010 Brier, +0.1pp
                         accuracy
  shrunk        0.5871   t=-2.4. 5% toward league base rates — a hair of
                         over-confidence, worth almost nothing
  temperature   0.5874   T=1.00: production is already well calibrated
  stacked       0.5859   t=-4.8 — the best model here: production, 20%
                         Elo-on-xG, then a 5% pull toward the pair's
                         record. Both ingredients say the same thing in
                         different ways (this club is further ahead of
                         that one than one season of xG suggests), and
                         together they are worth -0.0015 Brier: a 0.25%
                         improvement, and no better at picking winners

So the model family is not the bottleneck; the inputs are. Anything that
tells the model something xG cannot (lineups, rest days, congestion,
motivation) is worth more than another way of arranging the same numbers.

The one theme that did repeat: every ingredient that helped — Elo, the
head-to-head record — worked by adding long-run evidence about the gap
between two clubs, which pointed at the memory settings rather than at
any of these models. `python model_lab.py memory` followed that up and
ended the story: half-life 180 with a 1400-day lookback scores 0.5860
against the old 240/400's 0.5874 on identical fixtures (t=-3.8) — the
whole stacked gain, from two constants instead of two subsystems, so
NOTHING in this file ships and build_report just remembers for longer.
A hard 400-day cutoff had been doing the job of decay badly; with a
proper half-life the tail costs nothing and carries a little signal.
The train surface is flat from ~730 days on, so the setting is not a
knife edge, and the territory term was re-swept underneath it (still
0.15, `python model_lab.py deep`). Side effect: 393 more historical
fixtures become predictable — clubs 400-1400 days out of the top flight
— and they score Brier 0.586 against 0.648 for guessing, so they are
worth publishing rather than leaving blank.

Usage:
    python model_lab.py            # everything (a few minutes)
    python model_lab.py fast       # skip the fitted model (the slow one)
    python model_lab.py memory     # only the half-life / lookback grid
"""

import math
import sqlite3
import sys
from bisect import bisect_left
from collections import defaultdict

from build_report import (
    DB_PATH,
    PREDICT_DEEP_POWER,
    PREDICT_HALF_LIFE_DAYS,
    PREDICT_LOOKBACK_DAYS,
    _poisson_vec,
)
from backtest import (
    GOALS_BLEND,
    MIN_PRIOR_MATCHES,
    _ordinal,
    index_histories,
    league_rows,
    load_team_rows,
    pair_matches,
)

TRAIN_END = _ordinal("2021-07-01")   # coefficients may only see earlier matches
FIT_ITERATIONS = 8                   # iterative scaling converges well before this
ELO_START = 1500.0
ELO_SEASON_REGRESSION = 0.8          # carry-over between seasons


def attack_value(r):
    return (1 - GOALS_BLEND) * r["npxg"] + GOALS_BLEND * r["scored"]


def defence_value(r):
    return (1 - GOALS_BLEND) * r["npxga"] + GOALS_BLEND * r["missed"]


# ---------------------------------------------------------------- production

def weighted_profile(history, as_of_ord,
                     half_life=PREDICT_HALF_LIFE_DAYS,
                     lookback=PREDICT_LOOKBACK_DAYS):
    """(attack, defence, deep-or-None, n) from rows strictly before as_of."""
    att = dfn = w_sum = 0.0
    deep_sum = deep_w = 0.0
    n = 0
    for r in history:
        if r["ord"] >= as_of_ord:
            break
        age = as_of_ord - r["ord"]
        if age > lookback:
            continue
        w = 0.5 ** (age / half_life)
        att += w * attack_value(r)
        dfn += w * defence_value(r)
        w_sum += w
        n += 1
        if r["deep"] is not None:
            deep_sum += w * r["deep"]
            deep_w += w
    if n < MIN_PRIOR_MATCHES or w_sum <= 0:
        return None
    return att / w_sum, dfn / w_sum, (deep_sum / deep_w if deep_w else None), n


def league_context(rows, as_of_ord,
                   half_life=PREDICT_HALF_LIFE_DAYS,
                   lookback=PREDICT_LOOKBACK_DAYS, cache={}):
    """(mu, home_adv, league mean deep) over league rows before as_of."""
    key = (id(rows), as_of_ord, half_life, lookback)
    if key in cache:
        return cache[key]
    sums = {"h": [0.0, 0.0], "a": [0.0, 0.0]}
    deep_sum = deep_w = 0.0
    for r in rows:
        if r["ord"] >= as_of_ord:
            break
        age = as_of_ord - r["ord"]
        if age > lookback:
            continue
        w = 0.5 ** (age / half_life)
        if r["ha"] in sums:
            sums[r["ha"]][0] += w * attack_value(r)
            sums[r["ha"]][1] += w
        if r["deep"] is not None:
            deep_sum += w * r["deep"]
            deep_w += w
    total_v = sums["h"][0] + sums["a"][0]
    total_w = sums["h"][1] + sums["a"][1]
    mu = total_v / total_w if total_w else 0.0
    home_adv = 1.0
    if sums["h"][1] and sums["a"][1] and sums["a"][0]:
        home_adv = (sums["h"][0] / sums["h"][1]) / (sums["a"][0] / sums["a"][1])
    out = (mu, home_adv, deep_sum / deep_w if deep_w else None)
    cache[key] = out
    return out


def production_lambdas(match, hist, per_league,
                       half_life=PREDICT_HALF_LIFE_DAYS,
                       lookback=PREDICT_LOOKBACK_DAYS,
                       deep_power=PREDICT_DEEP_POWER):
    """Exactly what build_report.predictions_block computes, replayed."""
    league, as_of = match["league"], match["ord"]
    home = weighted_profile(hist[(league, match["home"])], as_of,
                            half_life, lookback)
    away = weighted_profile(hist[(league, match["away"])], as_of,
                            half_life, lookback)
    if not home or not away:
        return None
    mu, home_adv, lg_deep = league_context(per_league[league], as_of,
                                           half_life, lookback)
    if mu <= 0:
        return None
    sqrt_ha = math.sqrt(home_adv)
    lam_h = home[0] * away[1] / mu * sqrt_ha
    lam_a = away[0] * home[1] / mu / sqrt_ha
    if lg_deep and deep_power and home[2] is not None and away[2] is not None:
        lam_h *= (home[2] / lg_deep) ** deep_power
        lam_a *= (away[2] / lg_deep) ** deep_power
    return max(0.1, min(6.0, lam_h)), max(0.1, min(6.0, lam_a))


# ------------------------------------------------------------------- Poisson

def poisson_probs(lam_h, lam_a, rho=0.0):
    """Outcome probabilities, optionally with the Dixon-Coles low-score
    correction (rho = 0 is plain independent Poisson)."""
    ph, pa = _poisson_vec(lam_h), _poisson_vec(lam_a)
    home = draw = away = 0.0
    for i, pi in enumerate(ph):
        for j, pj in enumerate(pa):
            p = pi * pj
            if rho and i < 2 and j < 2:
                if i == 0 and j == 0:
                    p *= 1 - lam_h * lam_a * rho
                elif i == 0 and j == 1:
                    p *= 1 + lam_h * rho
                elif i == 1 and j == 0:
                    p *= 1 + lam_a * rho
                else:
                    p *= 1 - rho
                p = max(p, 1e-12)
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    total = home + draw + away
    return home / total, draw / total, away / total


# ------------------------------------------------------------ head-to-head

def h2h_residuals(evals, base_lams, half_life, venue_only, min_meetings):
    """Per-match summary of how the two clubs have historically performed
    against *each other*, relative to what the model expected of them.

    For every earlier meeting of the same pair, each club's residual is
    log(what it actually produced / what the model predicted it would).
    A club that reliably beats the model against this one opponent — the
    "bogey team" effect, if it exists — carries a positive mean residual.
    Only meetings strictly before the match are ever used.
    """
    chronological = sorted(range(len(evals)), key=lambda i: evals[i]["ord"])
    history = defaultdict(list)
    out = [None] * len(evals)
    for i in chronological:
        e = evals[i]
        m = e["match"]
        lam_h, lam_a = base_lams[i]
        key = (m["league"], tuple(sorted((m["home"], m["away"]))))
        rh = ra = w_sum = 0.0
        n = 0
        for past_ord, past_home, resid in history[key]:
            if venue_only and past_home != m["home"]:
                continue
            w = 0.5 ** ((e["ord"] - past_ord) / half_life) if half_life else 1.0
            rh += w * resid[m["home"]]
            ra += w * resid[m["away"]]
            w_sum += w
            n += 1
        out[i] = ((rh / w_sum, ra / w_sum)
                  if n >= min_meetings and w_sum > 0 else (0.0, 0.0))
        history[key].append((e["ord"], m["home"], {
            m["home"]: math.log(max(attack_value(m["hrow"]), 0.05) / lam_h),
            m["away"]: math.log(max(attack_value(m["arow"]), 0.05) / lam_a),
        }))
    return out


def h2h_surprise(evals, probs, half_life, min_meetings):
    """The confound-free version of the outcome form.

    A pair's raw head-to-head record mostly encodes how far apart the two
    clubs are — which the model already knows. This instead accumulates
    what the model got WRONG about their earlier meetings (actual result
    minus predicted probability), so only the part of the pair's history
    the model failed to anticipate can feed back in. If the bogey-team
    effect is real, this carries it; if the raw form only works because
    it re-states the quality gap, this collapses to nothing.
    """
    chronological = sorted(range(len(evals)), key=lambda i: evals[i]["ord"])
    history = defaultdict(list)
    out = [None] * len(evals)
    for i in chronological:
        e = evals[i]
        m = e["match"]
        key = (m["league"], tuple(sorted((m["home"], m["away"]))))
        acc = [0.0, 0.0, 0.0]
        w_sum = 0.0
        n = 0
        for past_ord, past_home, surprise in history[key]:
            w = 0.5 ** ((e["ord"] - past_ord) / half_life) if half_life else 1.0
            s = surprise if past_home == m["home"] else surprise[::-1]
            for j in range(3):
                acc[j] += w * s[j]
            w_sum += w
            n += 1
        out[i] = ([a / w_sum for a in acc]
                  if n >= min_meetings and w_sum > 0 else None)
        p = probs[i]
        history[key].append((e["ord"], m["home"],
                             [(1.0 if j == e["outcome"] else 0.0) - p[j]
                              for j in range(3)]))
    return out


def h2h_outcomes(evals, half_life, venue_only, min_meetings):
    """The other way to use head-to-head: the plain historical outcome
    split of the pair's earlier meetings (home win / draw / away win from
    this fixture's perspective), to be blended into the model's own."""
    chronological = sorted(range(len(evals)), key=lambda i: evals[i]["ord"])
    history = defaultdict(list)
    out = [None] * len(evals)
    for i in chronological:
        e = evals[i]
        m = e["match"]
        key = (m["league"], tuple(sorted((m["home"], m["away"]))))
        counts = [0.0, 0.0, 0.0]
        w_sum = 0.0
        n = 0
        for past_ord, past_home, outcome in history[key]:
            if venue_only and past_home != m["home"]:
                continue
            w = 0.5 ** ((e["ord"] - past_ord) / half_life) if half_life else 1.0
            # flip the result when the earlier meeting was the other way round
            idx = outcome if past_home == m["home"] else (2 - outcome)
            counts[idx] += w
            w_sum += w
            n += 1
        out[i] = ([c / w_sum for c in counts]
                  if n >= min_meetings and w_sum > 0 else None)
        history[key].append((e["ord"], m["home"], e["outcome"]))
    return out


# -------------------------------------------------- jointly fitted strengths

def fit_strengths(window, sqrt_ha):
    """Weighted Poisson fit of attack/defence by iterative scaling.

    window: (weight, home, away, value_home, value_away) tuples. Solves
    value_home ~ att[home] * dfn[away] * sqrt_ha and the mirror, so a club
    that ran up its numbers against the league's worst defence is discounted
    for it — which averaging cannot do.
    """
    teams = set()
    for _, h, a, _, _ in window:
        teams.add(h)
        teams.add(a)
    if not teams:
        return None
    level = math.sqrt(
        sum(w * (vh + va) for w, _, _, vh, va in window)
        / max(sum(2 * w for w, _, _, _, _ in window), 1e-9)
    )
    att = {t: level for t in teams}
    dfn = {t: level for t in teams}
    for _ in range(FIT_ITERATIONS):
        num = defaultdict(float)
        den = defaultdict(float)
        for w, h, a, vh, va in window:
            num[h] += w * vh
            den[h] += w * dfn[a] * sqrt_ha
            num[a] += w * va
            den[a] += w * dfn[h] / sqrt_ha
        for t in teams:
            if den[t] > 1e-9:
                att[t] = num[t] / den[t]
        num.clear()
        den.clear()
        for w, h, a, vh, va in window:
            num[a] += w * vh                      # away side conceded value_home
            den[a] += w * att[h] * sqrt_ha
            num[h] += w * va
            den[h] += w * att[a] / sqrt_ha
        for t in teams:
            if den[t] > 1e-9:
                dfn[t] = num[t] / den[t]
    return att, dfn


def fitted_lambdas(match, league_matches, ords, per_league):
    """Same pipeline as production, but strengths come from fit_strengths.

    Eligibility needs no separate test: only matches production could
    already predict reach this function, so both clubs are known to have
    at least MIN_PRIOR_MATCHES inside the window.
    """
    league, as_of = match["league"], match["ord"]
    mu, home_adv, _ = league_context(per_league[league], as_of)
    if mu <= 0:
        return None
    sqrt_ha = math.sqrt(home_adv)
    fit = _fit_cache_get(league, as_of, league_matches, ords, sqrt_ha)
    if not fit:
        return None
    att, dfn = fit
    if match["home"] not in att or match["away"] not in att:
        return None
    lam_h = att[match["home"]] * dfn[match["away"]] * sqrt_ha
    lam_a = att[match["away"]] * dfn[match["home"]] / sqrt_ha
    return max(0.1, min(6.0, lam_h)), max(0.1, min(6.0, lam_a))


_FIT_CACHE = {}


def _fit_cache_get(league, as_of, league_matches, ords, sqrt_ha):
    key = (league, as_of)
    if key in _FIT_CACHE:
        return _FIT_CACHE[key]
    hi = bisect_left(ords, as_of)
    lo = bisect_left(ords, as_of - PREDICT_LOOKBACK_DAYS)
    window = []
    for m in league_matches[lo:hi]:
        w = 0.5 ** ((as_of - m["ord"]) / PREDICT_HALF_LIFE_DAYS)
        window.append((w, m["home"], m["away"],
                       attack_value(m["hrow"]), attack_value(m["arow"])))
    fit = fit_strengths(window, sqrt_ha) if window else None
    _FIT_CACHE[key] = fit
    return fit


# ----------------------------------------------------------------------- Elo

def elo_diffs(matches_by_league, k, hfa, use_xg):
    """Rating difference (home perspective, home advantage included) for
    every match, computed strictly before that match is played."""
    diffs = {}
    for league, games in matches_by_league.items():
        rating = defaultdict(lambda: ELO_START)
        season = None
        for m in games:
            if m["season"] != season:
                season = m["season"]
                for t in list(rating):
                    rating[t] = ELO_START + ELO_SEASON_REGRESSION * (rating[t] - ELO_START)
            r_h, r_a = rating[m["home"]], rating[m["away"]]
            diff = r_h + hfa - r_a
            diffs[id(m)] = diff
            expected = 1.0 / (1.0 + 10 ** (-diff / 400.0))
            if use_xg:
                margin = attack_value(m["hrow"]) - attack_value(m["arow"])
                actual = max(0.0, min(1.0, 0.5 + margin / 4.0))
            else:
                actual = (1.0 if m["home_goals"] > m["away_goals"]
                          else 0.5 if m["home_goals"] == m["away_goals"] else 0.0)
            delta = k * (actual - expected)
            rating[m["home"]] = r_h + delta
            rating[m["away"]] = r_a - delta
    return diffs


def supremacy_probs(lam_h, lam_a, scale, theta):
    """Same expected goals, different link: instead of assuming the two
    scores are independent Poissons, map the expected-goal supremacy
    straight onto three outcomes with an ordered logistic whose draw band
    is learned from history."""
    return elo_probs(lam_h - lam_a, scale, theta)


def elo_probs(diff, scale, theta):
    """Ordered logistic: one latent strength axis cut into away/draw/home."""
    z = diff / scale
    p_home = 1.0 / (1.0 + math.exp(-(z - theta)))
    p_away = 1.0 / (1.0 + math.exp(-(-z - theta)))
    p_draw = max(1.0 - p_home - p_away, 1e-6)
    total = p_home + p_draw + p_away
    return p_home / total, p_draw / total, p_away / total


# ------------------------------------------------------------------- scoring

class Score:
    __slots__ = ("n", "brier", "logloss", "hits")

    def __init__(self):
        self.n = self.hits = 0
        self.brier = self.logloss = 0.0

    def add(self, probs, outcome):
        self.n += 1
        self.brier += sum((p - (1.0 if i == outcome else 0.0)) ** 2
                          for i, p in enumerate(probs))
        self.logloss += -math.log(max(probs[outcome], 1e-9))
        self.hits += int(max(range(3), key=lambda i: probs[i]) == outcome)

    def as_tuple(self):
        if not self.n:
            return (0, float("nan"), float("nan"), float("nan"))
        return (self.n, self.brier / self.n, self.logloss / self.n,
                self.hits / self.n * 100)


def score(evals, probs, era=None):
    s = Score()
    for e, p in zip(evals, probs):
        if era and e["era"] != era:
            continue
        s.add(p, e["outcome"])
    return s.as_tuple()


def brier_on(evals, probs, era):
    return score(evals, probs, era)[1]


def _brier_terms(evals, probs, era):
    out = []
    for e, p in zip(evals, probs):
        if e["era"] != era:
            continue
        out.append(sum((q - (1.0 if i == e["outcome"] else 0.0)) ** 2
                       for i, q in enumerate(p)))
    return out


def paired_t(evals, probs, base_probs):
    """t-statistic of the per-match Brier difference vs the baseline on the
    test era. Same matches, same outcomes, so pairing removes almost all
    the variance — |t| under ~2 means the difference is noise."""
    a = _brier_terms(evals, probs, "test")
    b = _brier_terms(evals, base_probs, "test")
    diffs = [x - y for x, y in zip(a, b)]
    n = len(diffs)
    if n < 2:
        return 0.0, 0.0
    mean = sum(diffs) / n
    var = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    se = math.sqrt(var / n)
    return mean, (mean / se if se else 0.0)


def report(name, evals, probs, base_probs=None):
    tr = score(evals, probs, "train")
    te = score(evals, probs, "test")
    line = (f"  {name:14s} train Brier {tr[1]:.4f} acc {tr[3]:4.1f}%   "
            f"|  test Brier {te[1]:.4f} log-loss {te[2]:.4f} acc {te[3]:4.1f}%")
    if base_probs is not None and probs is not base_probs:
        mean, t = paired_t(evals, probs, base_probs)
        line += f"  |  vs production {mean:+.4f} (t={t:+.1f})"
    print(line)


# ------------------------------------------------------------------ main run

def memory_sweep(matches, hist, per_league):
    """How long should the model remember? Both knobs at once — how far
    back rows are allowed to come from, and how fast they fade.

    Every other experiment that helped worked by adding long-run evidence
    about a club's level, so the honest first question is whether the
    shipped window is simply too short. Same era discipline: the grid is
    read on the train era, the winner is confirmed on the test era.
    """
    print("Memory sweep — train-era Brier (production is 240 / 400):\n")
    header = "  half-life |" + "".join(f"{lb:>10}" for lb in MEMORY_LOOKBACKS)
    print(header)
    print("  " + "-" * (len(header) - 2))
    scored = {}
    for half_life in MEMORY_HALF_LIVES:
        cells = []
        for lookback in MEMORY_LOOKBACKS:
            if lookback < half_life:          # a window shorter than the
                cells.append("        –")     # half-life is not a real config
                continue
            evals, probs = [], []
            for m in matches:
                lam = production_lambdas(m, hist, per_league, half_life, lookback)
                if not lam:
                    continue
                evals.append({
                    "league": m["league"], "ord": m["ord"],
                    "era": "train" if m["ord"] < TRAIN_END else "test",
                    "outcome": (0 if m["home_goals"] > m["away_goals"]
                                else 1 if m["home_goals"] == m["away_goals"] else 2),
                })
                probs.append(poisson_probs(*lam))
            train = score(evals, probs, "train")
            test = score(evals, probs, "test")
            scored[(half_life, lookback)] = (train, test, len(evals))
            cells.append(f"{train[1]:10.4f}")
        print(f"  {half_life:9d} |" + "".join(cells))
    print("\n  (match counts differ between configs — a longer lookback makes "
          "more\n   early-season fixtures predictable, so these Brier scores "
          "are not\n   strictly like-for-like; the shortlist below fixes that)")

    shortlist = sorted(scored, key=lambda key: scored[key][0][1])[:3]
    shipped_key = (PREDICT_HALF_LIFE_DAYS, PREDICT_LOOKBACK_DAYS)
    if shipped_key not in shortlist:
        shortlist.append(shipped_key)
    print("\n  Like-for-like on the held-out era — only the matches EVERY "
          "config\n  on the shortlist could predict, paired against shipped "
          "240/400:\n")
    detail = {key: config_probs(matches, hist, per_league, *key)
              for key in shortlist}
    common = set.intersection(*(set(d) for d in detail.values()))
    common = [mid for mid in detail[shipped_key] if mid in common]
    base = [detail[shipped_key][mid][0] for mid in common]
    outcomes = [detail[shipped_key][mid][1] for mid in common]
    eras = [detail[shipped_key][mid][2] for mid in common]
    evals = [{"era": e, "outcome": o} for e, o in zip(eras, outcomes)]
    for key in shortlist:
        probs = [detail[key][mid][0] for mid in common]
        tag = f"{key[0]}/{key[1]}" + (" (shipped)" if key == shipped_key else "")
        report(tag, evals, probs, base)


MEMORY_HALF_LIVES = (120, 150, 180, 240, 320, 450, 650)
MEMORY_LOOKBACKS = (400, 730, 1000, 1400, 1800, 2400)


def deep_recheck(matches, hist, per_league, half_life, lookback):
    """The territory term's exponent was chosen under the old 240/400
    memory. A longer memory changes what the strengths already capture,
    so the coefficient has to re-earn its place under the new one."""
    print(f"\nRe-checking the deep-completions term under {half_life}/{lookback}:")
    base = None
    for power in (0.0, 0.05, 0.10, 0.15, 0.20, 0.30):
        detail = config_probs(matches, hist, per_league, half_life, lookback,
                              deep_power=power)
        keys = list(detail)
        evals = [{"era": detail[k][2], "outcome": detail[k][1]} for k in keys]
        probs = [detail[k][0] for k in keys]
        if base is None:
            base = (keys, evals, probs)
        train = score(evals, probs, "train")
        test = score(evals, probs, "test")
        mark = "  <- shipped" if abs(power - PREDICT_DEEP_POWER) < 1e-9 else ""
        print(f"    power {power:.2f}  train Brier {train[1]:.4f}   "
              f"test Brier {test[1]:.4f}{mark}")


def config_probs(matches, hist, per_league, half_life, lookback,
                 deep_power=PREDICT_DEEP_POWER):
    """match id -> (probs, outcome, era) for one memory setting."""
    out = {}
    for m in matches:
        lam = production_lambdas(m, hist, per_league, half_life, lookback,
                                 deep_power)
        if not lam:
            continue
        out[id(m)] = (
            poisson_probs(*lam),
            (0 if m["home_goals"] > m["away_goals"]
             else 1 if m["home_goals"] == m["away_goals"] else 2),
            "train" if m["ord"] < TRAIN_END else "test",
        )
    return out


def main():
    fast = "fast" in sys.argv[1:]
    memory = "memory" in sys.argv[1:]
    deep = "deep" in sys.argv[1:]
    db = sqlite3.connect(DB_PATH)
    team_rows = load_team_rows(db)
    matches = pair_matches(team_rows)
    hist = index_histories(team_rows)
    per_league = league_rows(team_rows)
    matches.sort(key=lambda m: (m["league"], m["ord"], m["home"]))
    by_league = defaultdict(list)
    for m in matches:
        by_league[m["league"]].append(m)
    ords_by_league = {lg: [m["ord"] for m in ms] for lg, ms in by_league.items()}

    print(f"{len(team_rows)} team-match rows -> {len(matches)} paired matches")
    if memory:
        memory_sweep(matches, hist, per_league)
        return
    if deep:
        deep_recheck(matches, hist, per_league, 180, 1400)
        return
    print("Building the production baseline...")
    evals, base_lams = [], []
    for m in matches:
        lam = production_lambdas(m, hist, per_league)
        if not lam:
            continue
        evals.append({
            "match": m, "league": m["league"], "ord": m["ord"],
            "era": "train" if m["ord"] < TRAIN_END else "test",
            "outcome": (0 if m["home_goals"] > m["away_goals"]
                        else 1 if m["home_goals"] == m["away_goals"] else 2),
        })
        base_lams.append(lam)
    n_train = sum(1 for e in evals if e["era"] == "train")
    print(f"  {len(evals)} predictable matches "
          f"({n_train} train / {len(evals) - n_train} test)\n")

    # base rates come from the train era only — the test era must stay unseen
    base_rates = defaultdict(lambda: [0, 0, 0])
    overall = [0, 0, 0]
    for e in evals:
        if e["era"] == "train":
            base_rates[e["league"]][e["outcome"]] += 1
            overall[e["outcome"]] += 1
    fallback = [c / (sum(overall) or 1) for c in overall]
    rates = defaultdict(lambda: fallback)
    for lg, c in base_rates.items():
        total = sum(c)
        if total >= 100:
            rates[lg] = [x / total for x in c]

    results = {}
    results["production"] = [poisson_probs(lh, la) for lh, la in base_lams]
    results["base rates"] = [rates[e["league"]] for e in evals]

    # --- Dixon-Coles: sweep rho on the train era only. Negative rho is the
    # draw-fattening direction of the original paper; positive is allowed
    # so the data can reject the correction outright.
    print("Dixon-Coles low-score correction (rho swept on train era):")
    best_rho, best_b = 0.0, brier_on(evals, results["production"], "train")
    for rho in (-0.20, -0.15, -0.12, -0.09, -0.06, -0.03, 0.03, 0.06):
        probs = [poisson_probs(lh, la, rho) for lh, la in base_lams]
        b = brier_on(evals, probs, "train")
        print(f"    rho {rho:.2f}  train Brier {b:.4f}")
        if b < best_b:
            best_rho, best_b = rho, b
    print(f"  -> rho = {best_rho:.2f}\n")
    results["dixon-coles"] = ([poisson_probs(lh, la, best_rho) for lh, la in base_lams]
                              if best_rho else list(results["production"]))

    # --- jointly fitted strengths
    if not fast:
        print("Fitting attack/defence jointly (iterative scaling)...")
        fitted = []
        misses = 0
        for e, fallback in zip(evals, base_lams):
            m = e["match"]
            lam = fitted_lambdas(m, by_league[m["league"]],
                                 ords_by_league[m["league"]], per_league)
            if not lam:
                lam = fallback
                misses += 1
            fitted.append(poisson_probs(*lam))
        results["fitted"] = fitted
        print(f"  {misses} matches fell back to the production strengths\n")

    # --- Elo, both flavours
    print("Elo (rating and mapping parameters swept on train era):")
    elo_best = {}
    train_mask = [e["era"] == "train" for e in evals]
    train_outcomes = [e["outcome"] for e in evals if e["era"] == "train"]
    def train_brier(d_train, scale, theta):
        b = 0.0
        for x, outcome in zip(d_train, train_outcomes):
            p = elo_probs(x, scale, theta)
            b += sum((q - (1.0 if i == outcome else 0.0)) ** 2
                     for i, q in enumerate(p))
        return b / len(d_train)

    for use_xg in (False, True):
        name = "elo-xg" if use_xg else "elo"
        # two stages rather than a full grid: the rating parameters and the
        # mapping parameters barely interact, and the full grid is 9x slower
        scale, theta = 250.0, 0.45
        best = None
        for k in (10, 15, 20, 25, 30, 40, 50, 65):
            for hfa in (40, 60, 80, 100, 120):
                diffs = elo_diffs(by_league, k, hfa, use_xg)
                d_train = [diffs[id(e["match"])]
                           for e, t in zip(evals, train_mask) if t]
                b = train_brier(d_train, scale, theta)
                if best is None or b < best[0]:
                    best = (b, k, hfa, d_train)
        _, k, hfa, d_train = best
        best = None
        for scale in (100.0, 130.0, 150.0, 200.0, 250.0, 300.0, 400.0):
            for theta in (0.25, 0.35, 0.45, 0.55, 0.65, 0.8):
                b = train_brier(d_train, scale, theta)
                if best is None or b < best[0]:
                    best = (b, scale, theta)
        b, scale, theta = best
        print(f"    {name:6s} K={k} home={hfa} scale={scale} theta={theta} "
              f"-> train Brier {b:.4f}")
        diffs = elo_diffs(by_league, k, hfa, use_xg)
        probs = [elo_probs(diffs[id(e["match"])], scale, theta) for e in evals]
        results[name] = probs
        elo_best[name] = probs
    print()

    # --- head-to-head: does a pair's own history add anything to the
    # strengths? Both the residual form ("this club over-performs against
    # that one") and the folk form (the raw historical outcome split).
    print("Head-to-head factor (strength swept on train era):")
    h2h_best = None
    for half_life, hl_label in ((None, "flat"), (1100, "hl 3y"), (550, "hl 18m")):
        for venue_only in (False, True):
            for min_meetings in (2, 4):
                resid = h2h_residuals(evals, base_lams, half_life,
                                      venue_only, min_meetings)
                used = sum(1 for r in resid if r != (0.0, 0.0))
                for k in (0.05, 0.1, 0.2, 0.35, 0.5):
                    probs = []
                    for (lh, la), (rh, ra) in zip(base_lams, resid):
                        probs.append(poisson_probs(
                            max(0.1, min(6.0, lh * math.exp(k * rh))),
                            max(0.1, min(6.0, la * math.exp(k * ra)))))
                    b = brier_on(evals, probs, "train")
                    if h2h_best is None or b < h2h_best[0]:
                        h2h_best = (b, hl_label, venue_only, min_meetings, k,
                                    used, probs)
    b, hl_label, venue_only, min_meetings, k, used, probs = h2h_best
    print(f"    residual form: {hl_label}, "
          f"{'same venue only' if venue_only else 'either venue'}, "
          f"min {min_meetings} meetings, strength {k} -> train Brier {b:.4f} "
          f"({used}/{len(evals)} matches had enough history)")
    results["h2h-resid"] = probs

    out_best = None
    for half_life, hl_label in ((None, "flat"), (1100, "hl 3y")):
        for min_meetings in (2, 4, 6):
            rates_h2h = h2h_outcomes(evals, half_life, False, min_meetings)
            for w in (0.05, 0.1, 0.2, 0.3):
                probs = []
                for pr, hr in zip(results["production"], rates_h2h):
                    if hr is None:
                        probs.append(pr)
                    else:
                        probs.append([(1 - w) * p + w * q for p, q in zip(pr, hr)])
                b = brier_on(evals, probs, "train")
                if out_best is None or b < out_best[0]:
                    out_best = (b, hl_label, min_meetings, w, probs, rates_h2h)
    b, hl_label, min_meetings, w, probs, h2h_rates = out_best
    print(f"    outcome form:  {hl_label}, min {min_meetings} meetings, "
          f"weight {w} -> train Brier {b:.4f}")
    results["h2h-outcome"] = probs

    sur_best = None
    for half_life, hl_label in ((None, "flat"), (1100, "hl 3y")):
        for min_meetings in (2, 4, 6):
            surprise = h2h_surprise(evals, results["production"],
                                    half_life, min_meetings)
            for w in (0.05, 0.1, 0.2, 0.3, 0.5):
                probs = []
                for pr, s in zip(results["production"], surprise):
                    if s is None:
                        probs.append(pr)
                        continue
                    q = [max(p + w * d, 1e-4) for p, d in zip(pr, s)]
                    total = sum(q)
                    probs.append([x / total for x in q])
                b = brier_on(evals, probs, "train")
                if sur_best is None or b < sur_best[0]:
                    sur_best = (b, hl_label, min_meetings, w, probs)
    b, hl_label, min_meetings, w, probs = sur_best
    print(f"    surprise form: {hl_label}, min {min_meetings} meetings, "
          f"weight {w} -> train Brier {b:.4f}\n")
    results["h2h-surprise"] = probs

    # --- same expected goals, ordered-logistic link instead of Poisson
    print("Ordered logistic on expected-goal supremacy (swept on train era):")
    best = None
    for scale in (0.5, 0.7, 0.9, 1.1, 1.3, 1.6, 2.0):
        for theta in (0.25, 0.35, 0.45, 0.55, 0.65, 0.8):
            probs = [supremacy_probs(lh, la, scale, theta) for lh, la in base_lams]
            b = brier_on(evals, probs, "train")
            if best is None or b < best[0]:
                best = (b, scale, theta, probs)
    print(f"    scale {best[1]} theta {best[2]} -> train Brier {best[0]:.4f}\n")
    results["supremacy"] = best[3]

    # --- blends and calibration layers, all tuned on train only
    print("Blends and calibration (weights swept on train era):")
    prod = results["production"]
    for elo_name in ("elo", "elo-xg"):
        best = None
        for w in (0.5, 0.6, 0.7, 0.8, 0.9):
            probs = [[w * p + (1 - w) * q for p, q in zip(a, b)]
                     for a, b in zip(prod, elo_best[elo_name])]
            br = brier_on(evals, probs, "train")
            if best is None or br < best[0]:
                best = (br, w, probs)
        print(f"    production x {elo_name:6s} weight {best[1]:.1f} "
              f"-> train Brier {best[0]:.4f}")
        results[f"blend/{elo_name}"] = best[2]

    best = None
    for k in (0.0, 0.05, 0.1, 0.15, 0.2, 0.3):
        probs = [[(1 - k) * p + k * r for p, r in zip(pr, rates[e["league"]])]
                 for pr, e in zip(prod, evals)]
        br = brier_on(evals, probs, "train")
        if best is None or br < best[0]:
            best = (br, k, probs)
    print(f"    shrink to base rates k={best[1]:.2f} -> train Brier {best[0]:.4f}")
    results["shrunk"] = best[2]

    best = None
    for t in (0.8, 0.9, 1.0, 1.1, 1.2, 1.35):
        probs = []
        for pr in prod:
            q = [p ** (1 / t) for p in pr]
            s = sum(q)
            probs.append([x / s for x in q])
        br = brier_on(evals, probs, "train")
        if best is None or br < best[0]:
            best = (br, t, probs)
    print(f"    temperature T={best[1]:.2f} -> train Brier {best[0]:.4f}")
    results["temperature"] = best[2]

    # everything that helped at once: production, a dash of Elo-on-xG, a
    # dash of the pair's own record
    best = None
    for w in (0.03, 0.05, 0.1, 0.2):
        probs = []
        for pr, hr in zip(results["blend/elo-xg"], h2h_rates):
            probs.append(pr if hr is None
                         else [(1 - w) * p + w * q for p, q in zip(pr, hr)])
        br = brier_on(evals, probs, "train")
        if best is None or br < best[0]:
            best = (br, w, probs)
    print(f"    stacked (elo-xg blend + h2h weight {best[1]:.2f}) "
          f"-> train Brier {best[0]:.4f}\n")
    results["stacked"] = best[2]

    order = ["production", "dixon-coles", "fitted", "supremacy",
             "h2h-resid", "h2h-outcome", "h2h-surprise", "elo", "elo-xg",
             "blend/elo", "blend/elo-xg", "shrunk", "temperature",
             "stacked", "base rates"]
    print(f"Held-out comparison (test era = 2021-07-01 onwards, never tuned "
          f"on; n={sum(1 for e in evals if e['era'] == 'test')}):")
    for name in order:
        if name in results:
            report(name, evals, results[name], results["production"])


if __name__ == "__main__":
    main()
