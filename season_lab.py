"""Bench for the rolling season projection: does xG-weighted strength beat
just extrapolating the table?

The claim being tested is a specific one, and it is the whole reason the
projection is worth publishing: a club sitting third on 20 points with
mediocre underlying numbers should be projected DOWN, and a club sitting
tenth while creating more than it concedes should be projected UP. If
that is false, the honest thing to publish is points-per-game times games
remaining, and no model at all.

Method — replayed over every complete stored season, with no leakage:

  * Pair the per-team rows into fixtures (backtest.pair_matches).
  * Walk each season and stop at a series of checkpoints (10%, 20%, ...
    of the fixtures played). At each one, freeze the world: the only
    thing any projector may see is matches played strictly before the
    checkpoint date.
  * Project every team's FINAL points total, then score against what
    the season actually finished on. Metric is mean absolute error in
    points, which is the unit a reader thinks in.

Projectors raced:

  points      points-per-game so far, extrapolated. The naive table
              reading, and the thing to beat.
  xpts        Understat's expected points per game, extrapolated —
              the same extrapolation with luck stripped out.
  prior       strengths frozen at the season's first day: last season's
              opinion, never updated. Shows what the update is worth.
  model       production strengths recomputed at the checkpoint, run
              through the production Poisson over the remaining
              fixtures, expected points summed onto points banked.
  blend       model, shrunk toward the points extrapolation.
  flat        every team gets the league's mean points total. Floor.

Significance is a paired t-test across SEASONS (each season contributes
one mean-absolute-error number per projector), not across teams: teams
inside one season share the same fixtures and the same luck, so treating
them as independent would badly overstate confidence.

FINDINGS (58 finished seasons, mean absolute error in final points)

  played    flat  points   xpts   prior   model     t(model vs points)
      0%   13.77       –      –    8.20    8.20         (no table yet)
     10%   13.69   18.60  12.50    7.95    7.77             -24.4
     30%   13.69    8.93   8.07    6.61    5.95             -12.9
     50%   13.69    5.83   6.63    5.23    4.54             -11.7
     70%   13.69    3.89   6.18    3.85    3.44              -6.8
     90%   13.69    2.20   5.89    2.18    2.05              -5.4

  * The model wins at every checkpoint, and the held-out era after 2020
    agrees (t from -17.9 to -3.9). This is the result the feature rests
    on. At 0% the model IS the prior — nothing has happened yet — and
    only 53 seasons are scorable, because the first stored season of
    each league has no history in front of it.
  * After a tenth of a season, reading the table and multiplying is
    WORSE than assuming every club finishes on the league average
    (18.60 vs 13.69). Four matches of points is not evidence; the model
    knows this and the table does not.
  * Understat's xpts extrapolation is much better than points early and
    much worse late, because it throws away points that are already
    banked and can no longer be lost. Neither extreme is right, which is
    why production keeps banked points and simulates only what is left.
  * Never updating the preseason opinion ("prior") is startlingly close
    to the full model. The update is worth ~0.2 points at 10%, peaks
    near 0.7 around mid-season, and fades to 0.13 by 90%. Most of what
    the projection knows, it knew in August.

REJECTED, having been tested properly

  * Shrinking the model back toward the table extrapolation. Swept 0 to
    1; error fell monotonically as the shrink was removed, in both eras.
    Pure model, no shrinkage.
  * Dragging a just-promoted club's OLD top-flight record back toward
    the generic promoted prior, on the grounds that a club returning
    after a year down has been rebuilt. It only bites at the preseason
    checkpoint (a few matches in, the club's own new results dominate
    anyway). Both eras did prefer some shrink, but the training era
    picked 0.75 and the held-out era 0.5, the best held-out t was -1.8,
    and the effect on a whole league was 5.045 -> 5.040 points. Under
    this project's "|t| under ~2 is noise" rule that is not a result, so
    a returning club is still projected on the last top-flight football
    it actually played. `python season_lab.py stale` reproduces it.

Usage:
    python season_lab.py            # the full race, all checkpoints
    python season_lab.py promoted   # measure the newly-promoted prior
    python season_lab.py blend      # sweep the shrink toward the table
    python season_lab.py stale      # sweep the returning-club shrink
"""

import math
import sqlite3
import sys
from collections import defaultdict

from backtest import (
    _ordinal,
    index_histories,
    league_rows,
    load_team_rows,
    pair_matches,
)
from build_report import (
    DB_PATH,
    PREDICT_GOALS_BLEND,
    PREDICT_HALF_LIFE_DAYS,
    PREDICT_LOOKBACK_DAYS,
    _outcome_probs,
)

# Fraction of the season's fixtures played when we stop and project. 0.0 is
# the preseason projection — no ball kicked, prior evidence only — and it is
# the checkpoint the site spends every August sitting on, so it is scored
# like any other even though the table-reading baselines cannot run there.
CHECKPOINTS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)

MIN_PRIOR_MATCHES = 6   # matches backtest.py: fewer and a strength is noise
TRAIN_END_SEASON = 2020  # seasons <= this tune coefficients, later ones judge
STALE_GAP_DAYS = 200    # no top-flight match in this long means the club spent
                        # a season in the division below and came back up

# Strengths, as a multiple of the league average, for a club arriving with
# no usable top-flight history. Measured over the 80 such clubs in the data
# by `python season_lab.py promoted` — promoted sides are not merely a bit
# worse, they are lopsidedly worse at defending than at attacking, and
# guessing "slightly below average" flatters them badly.
PROMOTED_ATTACK = 0.787
PROMOTED_DEFENCE = 1.187


def att_of(r):
    return (1 - PREDICT_GOALS_BLEND) * r["npxg"] + PREDICT_GOALS_BLEND * r["scored"]


def def_of(r):
    return (1 - PREDICT_GOALS_BLEND) * r["npxga"] + PREDICT_GOALS_BLEND * r["missed"]


def strength_at(history, as_of_ord):
    """(attack, defence, n, days since last match) before as_of_ord, or None."""
    att_sum = def_sum = w_sum = 0.0
    n, last = 0, None
    for r in history:
        if r["ord"] >= as_of_ord:
            break
        last = r["ord"]
        age = as_of_ord - r["ord"]
        if age > PREDICT_LOOKBACK_DAYS:
            continue
        w = 0.5 ** (age / PREDICT_HALF_LIFE_DAYS)
        att_sum += w * att_of(r)
        def_sum += w * def_of(r)
        w_sum += w
        n += 1
    if n < MIN_PRIOR_MATCHES or w_sum <= 0:
        return None
    return att_sum / w_sum, def_sum / w_sum, n, as_of_ord - last


def resolve_strength(history, as_of_ord, mu, stale_gap, stale_w):
    """Strength for one club, handling the two awkward cases.

    A club with no usable history gets the measured promoted-side prior. A
    club whose last top-flight match is more than `stale_gap` days old — it
    spent a season in the division below — has a real record, but a record
    of a squad that has since been rebuilt and a level it just proved it
    could escape. `stale_w` is how far to drag that record back toward the
    promoted prior, and it is swept, not assumed.

    Returns (attack, defence, returning?).
    """
    prior = (PROMOTED_ATTACK * mu, PROMOTED_DEFENCE * mu)
    s = strength_at(history, as_of_ord)
    if s is None:
        return prior[0], prior[1], True
    att, dfn, _, gap = s
    if gap is not None and gap > stale_gap:
        # flagged as returning whatever stale_w is, so that sweeping the
        # weight compares the same clubs rather than a shifting cohort
        att = (1 - stale_w) * att + stale_w * prior[0]
        dfn = (1 - stale_w) * dfn + stale_w * prior[1]
        return att, dfn, True
    return att, dfn, False


_CTX_CACHE = {}


def context_at(rows, league, as_of_ord):
    """(mu, home_adv) over the league's rows strictly before as_of_ord."""
    key = (league, as_of_ord)
    if key in _CTX_CACHE:
        return _CTX_CACHE[key]
    sums = {"h": [0.0, 0.0], "a": [0.0, 0.0]}
    for r in rows:
        if r["ord"] >= as_of_ord:
            break
        age = as_of_ord - r["ord"]
        if age > PREDICT_LOOKBACK_DAYS:
            continue
        w = 0.5 ** (age / PREDICT_HALF_LIFE_DAYS)
        if r["ha"] in sums:
            sums[r["ha"]][0] += w * att_of(r)
            sums[r["ha"]][1] += w
    total_v = sums["h"][0] + sums["a"][0]
    total_w = sums["h"][1] + sums["a"][1]
    mu = total_v / total_w if total_w else 0.0
    home_adv = 1.0
    if sums["h"][1] and sums["a"][1] and sums["a"][0]:
        home_adv = (sums["h"][0] / sums["h"][1]) / (sums["a"][0] / sums["a"][1])
    _CTX_CACHE[key] = (mu, home_adv)
    return mu, home_adv


def expected_points(lam_home, lam_away):
    """(home E[pts], away E[pts]) for one fixture."""
    p_h, p_d, p_a = _outcome_probs(lam_home, lam_away)
    return 3 * p_h + p_d, 3 * p_a + p_d


def lambdas(home_s, away_s, mu, home_adv):
    sqrt_ha = math.sqrt(home_adv)
    lam_h = max(0.1, min(6.0, home_s[0] * away_s[1] / mu * sqrt_ha))
    lam_a = max(0.1, min(6.0, away_s[0] * home_s[1] / mu / sqrt_ha))
    return lam_h, lam_a


# ------------------------------------------------------------------ seasons

def build_seasons(matches):
    """(league, season) -> fixture list sorted by date."""
    seasons = defaultdict(list)
    for m in matches:
        seasons[(m["league"], m["season"])].append(m)
    for fixtures in seasons.values():
        fixtures.sort(key=lambda m: (m["ord"], m["home"]))
    return seasons


def is_complete(fixtures):
    """A finished double round robin, and nothing else.

    The projection is scored against a season's FINAL points, so a season
    still being played would be graded against a table that is simply not
    finished yet — every projector would look terrible and the model, which
    projects the whole season, worst of all. Requiring every club to have
    played 2*(n-1) matches throws out the live season and the COVID-
    abandoned Ligue 1 2019 without needing to name either.
    """
    played = defaultdict(int)
    for m in fixtures:
        played[m["home"]] += 1
        played[m["away"]] += 1
    if len(played) < 10:
        return False
    want = 2 * (len(played) - 1)
    return all(v == want for v in played.values())


def final_points(fixtures):
    pts = defaultdict(int)
    for m in fixtures:
        for t in (m["home"], m["away"]):
            pts[t] += 0
        if m["home_goals"] > m["away_goals"]:
            pts[m["home"]] += 3
        elif m["home_goals"] == m["away_goals"]:
            pts[m["home"]] += 1
            pts[m["away"]] += 1
        else:
            pts[m["away"]] += 3
    return dict(pts)


def table_at(fixtures, as_of_ord):
    """(points, played) per team over fixtures strictly before as_of_ord."""
    pts, played = defaultdict(int), defaultdict(int)
    for m in fixtures:
        if m["ord"] >= as_of_ord:
            break
        played[m["home"]] += 1
        played[m["away"]] += 1
        if m["home_goals"] > m["away_goals"]:
            pts[m["home"]] += 3
        elif m["home_goals"] == m["away_goals"]:
            pts[m["home"]] += 1
            pts[m["away"]] += 1
        else:
            pts[m["away"]] += 3
    return pts, played


def xpts_at(fixtures, as_of_ord):
    """Understat expected points banked per team before as_of_ord."""
    xp = defaultdict(float)
    ok = True
    for m in fixtures:
        if m["ord"] >= as_of_ord:
            break
        for row, team in ((m["hrow"], m["home"]), (m["arow"], m["away"])):
            v = row.get("xpts")
            if v is None:
                ok = False
            else:
                xp[team] += v
    return (dict(xp) if ok else None)


# -------------------------------------------------------------- projectors

def project(fixtures, as_of_ord, hist, per_league, league, blend_w,
            stale_gap=STALE_GAP_DAYS, stale_w=0.0):
    """All projections for one season at one checkpoint.

    Returns (dict of name -> {team: projected final points}, returning teams)
    or None when the checkpoint has nothing to project.
    """
    remaining = [m for m in fixtures if m["ord"] >= as_of_ord]
    if not remaining:
        return None
    pts, played = table_at(fixtures, as_of_ord)
    teams = sorted({t for m in fixtures for t in (m["home"], m["away"])})
    total_games = {t: 0 for t in teams}
    for m in fixtures:
        total_games[m["home"]] += 1
        total_games[m["away"]] += 1
    # a club that has not kicked off cannot be extrapolated from its table
    # row; at the preseason checkpoint that is every club, and the
    # table-reading baselines simply do not exist
    extrapolable = all(played[t] > 0 for t in teams)

    mu, home_adv = context_at(per_league[league], league, as_of_ord)
    if mu <= 0:
        return None

    # strengths frozen at the checkpoint, and at the season's first day
    season_start = fixtures[0]["ord"]
    prior_mu, prior_ha = context_at(per_league[league], league, season_start)
    if prior_mu <= 0:
        prior_mu, prior_ha = mu, home_adv
    now_s, prior_s, returning = {}, {}, set()
    for t in teams:
        h = hist[(league, t)]
        a, d, _ = resolve_strength(h, as_of_ord, mu, stale_gap, stale_w)
        now_s[t] = (a, d)
        a, d, ret = resolve_strength(h, season_start, prior_mu, stale_gap,
                                     stale_w)
        prior_s[t] = (a, d)
        if ret:
            returning.add(t)
    prior_mu, prior_ha = context_at(per_league[league], league, season_start)
    if prior_mu <= 0:
        prior_mu, prior_ha = mu, home_adv

    model = {t: float(pts[t]) for t in teams}
    prior = {t: float(pts[t]) for t in teams}
    for m in remaining:
        h, a = m["home"], m["away"]
        eh, ea = expected_points(*lambdas(now_s[h], now_s[a], mu, home_adv))
        model[h] += eh
        model[a] += ea
        eh, ea = expected_points(*lambdas(prior_s[h], prior_s[a],
                                          prior_mu, prior_ha))
        prior[h] += eh
        prior[a] += ea

    out = {"prior": prior, "model": model}
    mean_pts = sum(final_points(fixtures).values()) / len(teams)
    out["flat"] = {t: mean_pts for t in teams}

    if extrapolable:
        points = {t: pts[t] / played[t] * total_games[t] for t in teams}
        out["points"] = points
        out["blend"] = {t: blend_w * model[t] + (1 - blend_w) * points[t]
                        for t in teams}
        xp = xpts_at(fixtures, as_of_ord)
        if xp:
            out["xpts"] = {t: xp[t] / played[t] * total_games[t] for t in teams}
    return out, returning


# ------------------------------------------------------------------ scoring

def season_key(season):
    """Understat seasons are '2014'..'2025'; return the int for era splits."""
    try:
        return int(str(season)[:4])
    except ValueError:
        return 0


def paired_t(a, b):
    """t statistic of the paired differences a-b (negative favours a)."""
    diffs = [x - y for x, y in zip(a, b)]
    n = len(diffs)
    if n < 2:
        return 0.0
    mean = sum(diffs) / n
    var = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    if var <= 0:
        return 0.0
    return mean / math.sqrt(var / n)


def run_race(seasons, hist, per_league, blend_w=0.75, era=None, quiet=False,
             stale_w=0.0, only_returning=False):
    """MAE per projector per checkpoint. era: None|'train'|'test'.

    only_returning scores just the promoted and just-returned clubs, which is
    the only place the stale-history rule can possibly show up: averaged over
    twenty clubs, a change affecting two of them vanishes.
    """
    # per checkpoint: name -> list of per-season MAE
    results = defaultdict(lambda: defaultdict(list))
    counted = defaultdict(int)
    for (league, season), fixtures in sorted(seasons.items()):
        yr = season_key(season)
        if era == "train" and yr > TRAIN_END_SEASON:
            continue
        if era == "test" and yr <= TRAIN_END_SEASON:
            continue
        actual = final_points(fixtures)
        for frac in CHECKPOINTS:
            cut = int(len(fixtures) * frac)
            if cut >= len(fixtures):
                continue
            as_of_ord = fixtures[cut]["ord"]
            got = project(fixtures, as_of_ord, hist, per_league, league,
                          blend_w, stale_w=stale_w)
            if not got:
                continue
            projections, returning = got
            if only_returning:
                if not returning:
                    continue
                pick = returning
            else:
                pick = None
            for name, proj in projections.items():
                errs = [abs(proj[t] - actual[t]) for t in proj
                        if pick is None or t in pick]
                if errs:
                    results[frac][name].append(sum(errs) / len(errs))
            counted[frac] += 1
    if not quiet:
        report(results, counted)
    return results


def report(results, counted):
    names = ["flat", "points", "xpts", "prior", "model", "blend"]
    print(f"{'played':>7}  {'n':>3}  " + "  ".join(f"{n:>7}" for n in names)
          + "   t(model vs points)")
    print("-" * 86)
    for frac in CHECKPOINTS:
        if frac not in results:
            continue
        row = results[frac]
        cells = []
        for n in names:
            cells.append(f"{sum(row[n]) / len(row[n]):7.2f}" if row.get(n) else "      –")
        if row.get("model") and row.get("points"):
            t = f"{paired_t(row['model'], row['points']):+7.2f}"
        else:
            t = "      –"   # preseason: there is no table to extrapolate
        print(f"{frac * 100:6.0f}%  {counted[frac]:3d}  " + "  ".join(cells)
              + f"   {t}")
    print("\nmean absolute error in final league points (lower is better);"
          "\nt is a paired test across seasons, negative = model better,"
          "\n|t| under ~2 is noise.")


# --------------------------------------------------------------- promoted

def measure_promoted(seasons, hist, per_league):
    """What are clubs worth when they arrive with no top-flight history?

    Finds every team-season whose first match has no usable strength, then
    reports its actual attack/defence over that season relative to the
    league's mean, which is the scale production works in.
    """
    atts, defs, cases = [], [], []
    for (league, season), fixtures in sorted(seasons.items()):
        start = fixtures[0]["ord"]
        mu, _ = context_at(per_league[league], league, start)
        if mu <= 0:
            continue
        teams = sorted({t for m in fixtures for t in (m["home"], m["away"])})
        for t in teams:
            if strength_at(hist[(league, t)], start) is not None:
                continue
            rows = [r for r in hist[(league, t)]
                    if start <= r["ord"] <= fixtures[-1]["ord"]]
            if len(rows) < 10:
                continue
            a = sum(att_of(r) for r in rows) / len(rows) / mu
            d = sum(def_of(r) for r in rows) / len(rows) / mu
            atts.append(a)
            defs.append(d)
            cases.append((league, season, t, a, d))
    if not atts:
        print("no promoted-with-no-history cases found")
        return
    print(f"{len(atts)} clubs arrived with no usable history\n")
    for league, season, t, a, d in cases:
        print(f"  {league:16s} {season}  {t:24s} att {a:.2f}  def {d:.2f}")
    print(f"\n  mean attack  {sum(atts) / len(atts):.3f} x league average")
    print(f"  mean defence {sum(defs) / len(defs):.3f} x league average")
    print("\n  -> PROMOTED_ATTACK / PROMOTED_DEFENCE")


def sweep_blend(seasons, hist, per_league):
    """Shrink weight on the model vs the points extrapolation, tuned on the
    training era only, then read off on the held-out era."""
    weights = (0.0, 0.25, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 1.0)
    for era in ("train", "test"):
        print(f"\n--- {era} era ---")
        print(f"{'weight':>7}  " + "  ".join(f"{int(f * 100):5d}%" for f in CHECKPOINTS)
              + "     all")
        for w in weights:
            res = run_race(seasons, hist, per_league, blend_w=w, era=era, quiet=True)
            cells, pooled = [], []
            for frac in CHECKPOINTS:
                vals = res.get(frac, {}).get("blend")
                if vals:
                    cells.append(f"{sum(vals) / len(vals):6.2f}")
                    pooled += vals
                else:
                    cells.append("     –")
            overall = sum(pooled) / len(pooled) if pooled else 0.0
            print(f"{w:7.2f}  " + "  ".join(cells) + f"  {overall:6.3f}")


def sweep_stale(seasons, hist, per_league):
    """How far should a just-promoted club's OLD top-flight record be dragged
    back toward the generic promoted prior?

    0.0 trusts the stale record completely (what production does today), 1.0
    throws it away and treats the club as an unknown newcomer. Scored on the
    affected clubs only, tuned on the training era, read off on held-out.
    """
    for era in ("train", "test"):
        print(f"\n--- {era} era, promoted/returning clubs only ---")
        print(f"{'stale_w':>7}  "
              + "  ".join(f"{int(f * 100):5d}%" for f in CHECKPOINTS)
              + "     all   t(preseason vs off)")
        best, baseline = None, None
        for w in (0.0, 0.25, 0.5, 0.75, 1.0):
            res = run_race(seasons, hist, per_league, era=era, quiet=True,
                           stale_w=w, only_returning=True)
            cells, pooled = [], []
            for frac in CHECKPOINTS:
                vals = res.get(frac, {}).get("model")
                if vals:
                    cells.append(f"{sum(vals) / len(vals):6.2f}")
                    pooled += vals
                else:
                    cells.append("     –")
            overall = sum(pooled) / len(pooled) if pooled else 0.0
            pre = res.get(0.0, {}).get("model", [])
            if baseline is None:
                baseline, t = pre, "        –"
            else:
                t = f"{paired_t(pre, baseline):+9.2f}"
            print(f"{w:7.2f}  " + "  ".join(cells) + f"  {overall:6.3f}  {t}")
            if best is None or overall < best[1]:
                best = (w, overall)
        print(f"  best on this era: stale_w={best[0]}  ({best[1]:.3f})")

    print("\n--- whole-league effect of the best rule (all 20 clubs) ---")
    for w in (0.0, 0.5, 1.0):
        res = run_race(seasons, hist, per_league, era="test", quiet=True,
                       stale_w=w)
        pooled = [v for frac in CHECKPOINTS for v in res.get(frac, {}).get("model", [])]
        print(f"  stale_w={w:4.2f}  MAE {sum(pooled) / len(pooled):.3f}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "race"
    db = sqlite3.connect(DB_PATH)
    team_rows = load_team_rows(db)
    # xpts rides along for the extrapolation baseline; load_team_rows drops it
    xp = {}
    for season, league, team, day, v in db.execute(
        """SELECT season, league, team, match_date, xpts
           FROM understat_team_matches WHERE xpts IS NOT NULL"""
    ):
        xp[(league, season, team, day[:10])] = v
    for r in team_rows:
        r["xpts"] = xp.get((r["league"], r["season"], r["team"], r["day"]))

    matches = pair_matches(team_rows)
    hist = index_histories(team_rows)
    per_league = league_rows(team_rows)
    seasons = build_seasons(matches)
    complete = {k: v for k, v in seasons.items() if is_complete(v)}
    print(f"{len(matches)} paired matches -> {len(complete)} seasons "
          f"({', '.join(sorted({k[0] for k in complete}))})\n")

    if mode == "promoted":
        measure_promoted(complete, hist, per_league)
    elif mode == "blend":
        sweep_blend(complete, hist, per_league)
    elif mode == "stale":
        sweep_stale(complete, hist, per_league)
    else:
        print("=== all seasons ===")
        run_race(complete, hist, per_league)
        print("\n=== held-out era only (seasons after "
              f"{TRAIN_END_SEASON}) ===")
        run_race(complete, hist, per_league, era="test")


if __name__ == "__main__":
    main()
