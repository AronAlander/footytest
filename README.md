# Football Analytics
Not serious work, just trying some good'ol vibecoding.

Analytics tool for the **big five European leagues** — Serie A, Premier League,
La Liga, Bundesliga and Ligue 1 — **plus Allsvenskan**, built on free, no-signup
data sources. The dashboard has a league switcher; every tab (standings, team
analytics, players, insights) works for all five big leagues, and Allsvenskan
gets standings plus an xG-powered reduced set (see below).

## Quick start

Requires Python 3.10+ (standard library only, no dependencies):

```
python update.py
```

That runs the whole pipeline — the three fetchers below, then the report build.
Add `--push` (or just double-click `update.bat`) to also commit `docs/` and push,
which redeploys the live dashboard. The steps can of course be run individually:

```
python fetch_data.py
python fetch_understat.py
python fetch_fotmob.py
python fetch_preseason.py
python build_report.py
```

The season is picked automatically: both fetchers flip to the new campaign on
1 August (European seasons run autumn–spring), and the report scopes every table
and chart to each league's current season — older seasons stay in the database
as history but never mix into the dashboard. No annual maintenance needed.

Older seasons are browsable too: a **Season** dropdown in the header links to one
frozen archive page per past season. Understat's history goes back to 2014/15,
and `python fetch_understat.py --backfill` pulls all of it (~55 requests, a
couple of minutes). The dropdown groups them by competition, because a
calendar-year league's 2024 is not the big five's 2024/25 and one flat list
would have to misfile one of them:

- **Big five** (`docs/archive/2014-15.html` …) carry the four Understat tabs —
  team analytics, players, insights, Best of Europe — including the team
  head-to-head deep dive, but no League tab: Understat serves no fixture list,
  and TheSportsDB only serves the season being played, so there is nothing to
  rebuild a table or a results list from (backfilling it would take ~2,000
  throttled requests, an hour+ — skipped for now).
- **Allsvenskan** (`docs/archive/allsvenskan-2024.html` …) does get a League
  tab, because FotMob's per-match feed keeps both clubs and the scoreline: the
  final table is computed from those results, alongside the home/away split and
  every match of the season. Built entirely from rows already in the database —
  `scope_to_fotmob_season` derives the match list from `fotmob_team_matches`,
  so these pages need no extra fetch. What they leave out is the predictions,
  the season projection and the report card, which are all forecasts of a
  campaign still being played. Matchday numbers are left blank too: FotMob has
  no round column, and deriving one from each club's match count gets 62 of 77
  right against the live feed — an "R14" wrong one time in five is worse than
  no R14 at all.

`fetch_data.py` downloads league tables, results, and fixtures for all five leagues
and stores them in `football.sqlite` (matches are upserted; standings are saved as dated
snapshots, so history accumulates the more often you run it). Matches are fetched
round by round — the test key truncates the season/recent-results endpoints but
serves complete rounds — so the whole season lands in the database. The run takes
a few minutes because the test key allows only ~30 requests/minute. Raw API
responses are also kept in `data/` for debugging.

`fetch_understat.py` pulls advanced stats for all five leagues from Understat's
public JSON endpoint (no signup, one request per league): per-match team
xG/xGA/xPts/PPDA and per-player xG/xA for ~2,800 players. Understat only covers
the big five leagues plus Russia.

`fetch_fotmob.py` covers **Allsvenskan** from FotMob's unofficial JSON endpoints
(no key, no signing — verified 2026-07-17, but unofficial means it can break
without notice): full player leaderboards (xG, xA, xGOT, shots on target,
chances created; seasons back to 2017) and per-match team stats (xG, npxG,
xGOT, shots on target, possession) at one request per match — a season is ~240
matches, re-runs fetch only new results. Seasons 2023 onward are stored, which
is what the prediction lookback window (`PREDICT_LOOKBACK_DAYS`, 1400 days)
can actually reach: before that backfill the database held 2026 alone, so on
the season's opening weekend the model had no history for any club, shrank
every one of them to the league average, and projected the whole table to
finish on the same ~38 points. Those same three seasons are what the
Allsvenskan archive pages are built from. FotMob has no PPDA, deep completions,
xGChain/xGBuildup or xPts; xPts is computed here from each match's xG with a
Poisson model. In the dashboard Allsvenskan therefore gets the League tab, the
xG table, form curves, the team head-to-head deep dive (with a reduced radar)
and curated player boards — but not the full player explorer or the
pressing/territory charts, and it stays out of Best of Europe.

`fetch_preseason.py` pulls **club friendlies** for all six leagues' teams from the
same unofficial FotMob endpoints (one request per club, ~125 in total, about four
minutes). FotMob publishes no xG for friendlies, so this feeds a scores-and-schedule
view only: a seasonal **Preseason** tab in the dashboard that appears while
friendlies are being played (recent results and upcoming fixtures per league) and
retires itself once the window has passed.

`build_report.py` turns the database into a self-contained `report.html` — open it
in any browser (vanilla JavaScript, works offline from a double-click). It also
writes an identical copy to `docs/index.html`, which is committed so the report can
be served as a web dashboard (see below). A league switcher at the top flips the
whole dashboard between the five leagues (deep-linkable by prefixing any link with
`#lg=Premier_League&…`); below it are five tabs.

**The address bar follows you.** Every move writes itself into the hash —
league, tab, the open match, the clubs in the comparison (`#club=`) and the open
player card (`#player=`) — so any state on the site can be copied out of the
address bar, pasted back, and reloaded into the same view. Back and Forward walk
the same trail, and the player card has a **Copy link** button for the reader
who never looks at the address bar. Names that open something carry a dotted
underline, explained once under the search box rather than block by block.

A **search box** sits under the badges, and `/` or `Ctrl`+`K` opens it from
anywhere on the page (as does the floating button that appears once the header
has scrolled away). It covers every club, every player and every section of the
page, across all five leagues at once: a club opens in Team analytics, a player
opens their profile card, a section scrolls to itself in whichever league is on
screen. The index is built in the browser on first use out of data the page
already carries, so it costs nothing to ship and nothing to keep in step.
Accents are folded, so "martinez" finds Martínez.

- **League** — full standings computed from stored results (rank-trend arrows,
  W/D/L form chips), home/away split table, recent results, upcoming fixtures,
  and a **predictions block**: a small Poisson model over each club's
  recency-weighted xG turns the next ten fixtures into win/draw/win
  probability bars with an xG forecast (no predicted scoreline — chance
  quality says little about which exact score a match lands on). Home
  advantage is measured from the league's own home/away xG split; newly
  promoted clubs (no top-flight xG history) are honestly left unpredicted,
  and the block opens with a caveat that the model knows nothing about
  transfers, injuries or managers — a conversation starter, not betting
  advice. The model is validated by `python backtest.py`, which replays
  every stored season (21,700 matches back to 2014/15) predicting each
  match only from data available before it: Brier 0.583 and 53% outcome
  accuracy, against 0.647 / 44% for guessing by league base rates —
  approaching, not matching, bookmaker quality. The backtest chose the
  model's shape: strengths reach back 1400 days (nearly four seasons)
  with a 180-day half-life doing the forgetting, which beat every
  shorter window on held-out seasons — an old match should fade, not
  fall off a cliff — and as a bonus makes 393 more historical fixtures
  predictable, promoted clubs included, at no loss of accuracy on them.
  Team strengths are a 70/30 blend of non-penalty xG and actual
  goals — penalties are noise, finishing skill is real — which beat pure
  xG, pure npxG and every other blend weight tried. In the Understat
  leagues the attack is additionally scaled by a small deep-completions
  territory term (validated on held-out seasons: coefficient chosen on
  pre-2021 data, gain confirmed on 2021+); PPDA was screened the same
  way, showed zero extra signal beyond xG, and was left out. Allsvenskan
  has no deep-completions data, so its model simply omits the term.
  Underneath the predictions sits a **model report card**, which grades
  the site on calls it actually published rather than on a replay. Every
  prediction is written to `predictions/log.csv` when it is made and
  frozen the moment a result exists, so the card can show hit rate, Brier
  score, a calibration table (when it says 60%, does it happen 60% of the
  time?) and the most recent calls with their outcomes. The log is a
  committed CSV rather than a database table on purpose: `football.sqlite`
  is gitignored and lives in the Actions cache, which has been lost
  before, and a prediction record that can evaporate proves nothing. The
  card hides itself until matches have been graded and says plainly that
  anything under 30 calls is noise.
  `python model_lab.py` races alternative *model families* on the same
  replay (train pre-2021, verify on 2021+, paired t-test against the
  shipped model): a Dixon-Coles low-score correction, jointly fitted
  attack/defence, an ordered logistic on expected-goal supremacy, Elo on
  results, Elo on the xG margin, head-to-head history in three forms,
  blends and calibration layers. None of them is in the site: the two
  that did help out of sample (a dash of Elo-on-xG, a dash of the pair's
  head-to-head record) turned out to be saying the same thing — that a
  club's level is better evidenced over years than over one season — and
  once the memory sweep (`python model_lab.py memory`) lengthened the
  window, two constants captured the whole gain with no new machinery.
  Notably the "bogey team" effect does **not** survive its controls:
  head-to-head history helps only in the raw form that restates the
  quality gap, and collapses to noise once you feed back solely what the
  model got wrong about those meetings. Full numbers and the reason each
  alternative failed are in that file's docstring; the short version is
  that the model family is not the bottleneck, the inputs are.
- **Season projection** — a rolling forecast of the final table. Points
  already banked are carried over untouched; every fixture still to be
  played is simulated 5,000 times through the same Poisson model, and the
  final tables are counted into projected points plus title, top-four and
  bottom-three probabilities. Because the strengths behind it are mostly
  non-penalty xG, it disagrees with the table on purpose: a club third on
  a thin xG record keeps its points and is still projected to fade, and a
  good side stuck in mid-table is projected to climb. Before a season
  kicks off the block runs on prior seasons alone and says so. Newly
  promoted clubs with no top-flight history start from the measured
  average of promoted sides (0.79× league attack, 1.19× defence over the
  80 such arrivals in the data) rather than from nothing.
  `python season_lab.py` is the referee: it replays 58 finished seasons,
  projects the final table at ten points in each, and scores mean absolute
  error in points against extrapolating the table, extrapolating Understat
  xPts, never updating the preseason view, and a flat league-average
  baseline. The projection wins at every checkpoint (held-out paired t
  −17.9 to −3.9); after a tenth of a season it is 7.7 points out against
  18.8 for reading the table and multiplying, which at that stage is worse
  than assuming every club finishes on the league average. Two ideas were
  tested and rejected: shrinking the projection back toward the table
  (worse at every weight), and discounting a returning club's stale
  top-flight record toward the promoted prior (both eras preferred it, but
  the optima disagreed and the best held-out t was −1.8, so it stays out).
  Every build also appends a snapshot — projected points and the three
  probabilities, per team — to a committed log (`projection_log.py`,
  `predictions/projection_log.csv`, same reasoning as the prediction log:
  a database table would live in the Actions cache, which has already been
  lost once). A **projection over time** chart reads that log back: one
  small panel per team, its own line scaled to its own range so a
  mid-table club's real swings are as visible as a title contender's,
  colored green/red for whether the projection has risen or fallen since
  its first snapshot. `_compute_projection()` — the shared core behind both
  the live table and the log — takes an `as_of` date that excludes
  everything on or after it, including matches played since, which is what
  let `backfill_projection_log.py` reconstruct history for the season
  already in progress (Allsvenskan) the day this shipped, instead of
  starting the chart from a blank page.
  Two things came out of watching Allsvenskan's numbers for a day. First,
  a genuine bug: production's `_team_strengths` had no equivalent of
  `backtest.py`'s and `season_lab.py`'s `MIN_PRIOR_MATCHES` gate, so a
  team's very first match of a season could set its strength off a sample
  of one — caught when Hammarby's opening-day projection read 76 points,
  a number gone within a night. `PREDICT_MIN_MATCHES` now shrinks a thin
  sample toward the league average in proportion to how thin it is,
  restoring the lab's own minimum rather than introducing a new
  coefficient. Second, a real modeling question, checked rather than
  assumed: is heavy goals-over-npxG overperformance (Sirius sat at +14.2
  through half a season) mostly noise, as the model's 30%-goals blend
  implies? `python season_lab.py finishing` splits 1,170 Understat
  team-seasons in half and correlates first-half against second-half
  overperformance — weak overall (r=0.18), which is why discounting a hot
  streak is usually right, but the 18 team-seasons at Sirius's scale
  (Real Madrid, Bayern Munich twice, Juventus, Barcelona, Lazio three
  times) averaged +7.68 in their second half against a +1.92 population
  mean — regressing to about half the gap, not to zero. The 30% weight
  was tuned on aggregate Brier score, never specifically on this tail, so
  it may be under-crediting a club running this hot. Not shipped as a
  coefficient change — that would need the same train/test sweep as every
  other number here — but the finding is now a caveat on the page, and a
  thin-history caveat appears too whenever a league's own data falls well
  short of the model's intended four seasons (currently just Allsvenskan).
  The projection-over-time chart had its own small bug: an unstarted
  season's delta is Monte Carlo noise around zero, and Python's `"+.0f"`
  keeps the minus sign on a negative float that rounds to `0`
  (`f"{-0.3:+.0f}"` is `"-0"`), so one team could show `-0 pts` and a red
  line while every other team at the same effective zero showed `+0` —
  fixed by rounding the delta to an int before classifying sign and color,
  not after.
  Two more views read the same simulations without adding a new model.
  **How wide is that projection?** turns the Proj column's single number
  per team back into the distribution it was averaged from — a box (middle
  50% of simulated finishes) and whisker (middle 90%) per team, against
  the same top-1/top-4/bottom-3 zone shading as the table, so two clubs
  sharing a Proj of 55 stop looking identical when one of them is nailed
  on for mid-table and the other's finish is still anywhere from 7th to
  the drop. **Simulate one season** runs exactly one of the 5,000 draws
  instead of averaging them: every remaining fixture gets a real Poisson
  scoreline (client-side, from the same `lam_home`/`lam_away` numbers
  computed server-side and embedded as JSON — no second model, just one
  draw shown instead of summarized), built into a full P/W/D/L/GF/GA/GD/Pts
  table with a "Simulate again" button for a different draw. Both reuse
  `_compute_projection()`'s existing, already-validated simulation loop —
  the first by keeping a full rank histogram per team instead of only the
  three cutoff counters, the second by keeping the per-fixture expected
  goals that used to be discarded after building the margin sampler.
- **Matches** — one match at a time, in either tense, reachable by clicking any
  row in Recent results or Upcoming fixtures. An upcoming fixture gets the
  model's call, both sides' form and venue splits, the head-to-head and each
  squad's leading attackers. A match already played gets a report: the score,
  the **match stats** both feeds keep for those 90 minutes, what the chances
  deserved (Understat's post-match rerun), and what this site said beforehand,
  graded called it / missed. The stat rows differ by feed rather than being
  padded with blanks — the big five get expected goals, non-penalty xG, deep
  completions (passes completed within about 20 metres of goal), PPDA (lower is more
  pressing, so that bar favours the smaller number) and expected points;
  Allsvenskan gets expected goals, non-penalty xG, xG on target, shots,
  shots on target, possession and expected points as headline rows, and
  everything else FotMob measured behind a disclosure: big chances and big
  chances missed, touches in the opposition box, corners, xG split open play
  / set play, shots inside and outside the box, the woodwork, passes, pass
  accuracy, offsides, tackles, interceptions, blocks, clearances, keeper
  saves, duels and aerial duels won, successful dribbles, fouls and cards.
  Rows nobody wins — tackles, blocks, clearances, saves, fouls, cards — are
  drawn faint and unbolded, because all of them climb with time spent
  defending. Those extra columns are stored by `fetch_fotmob.py`, which keeps
  a `stats_version` per match and re-fetches any match stored under an older
  one, newest first and capped per run — the live database lives in the
  Actions cache and is never rebuilt, so that is the only way a new column
  reaches a match already on file. Unattended that covers the season being
  played, which is the only one the Matches tab reads; older seasons need
  `python fetch_fotmob.py --backfill --refresh-all` by hand. A stat line whose
  scoreline disagrees with the fixture feed is dropped rather than shown, on
  the same guard the xG headline uses: the two feeds would be describing
  different matches.
- **Team analytics** — xG table (points vs expected points), a team comparison
  block (pick 2–3 teams for a percentile radar over six style dimensions —
  attack, defence, finishing, pressing, territory, box defence — with the raw
  per-match numbers underneath, deep-linkable via `#club=A,B,C`),
  pressing-vs-territory scatter (PPDA against deep completions), rolling
  xG-difference form curves. Picking exactly **two** teams turns the comparison
  into a head-to-head deep dive: a tale-of-the-tape bar duel across ten metrics
  (bars split by league-percentile share), this season's actual meetings between
  the clubs with the score and both sides' xG, last-five form chips, points and
  npxGD split by home/away, and both teams' rolling form curves overlaid on one
  chart.
- **Players** — an explorer over every tracked player (~590): search, team /
  position / minutes filters, a per-90 toggle, and click-to-sort columns for
  goals, xG, G−xG, assists, xA, shots, key passes, xGChain, xGBuildup and more.
  The table shows the top 25 by the current sort, with show-more buttons for
  the rest, so the tab stays compact.
  Click any row for a profile card with season totals and per-90 percentile bars
  vs same-position peers; a comparison block overlays up to three players on a
  percentile radar (deep-linkable via `#player=Name` / `#compare=A,B,C`).
  The peer group is players with 450+ minutes, or — early in a season, before
  anyone has five matches — half the minutes of the league's busiest player, so
  a card in August ranks against a real group instead of dividing by zero. The
  comparison search spans **all five leagues**, so cross-league match-ups work
  (Haaland vs Lautaro, say) — each player is ranked against same-position peers
  in their own league, and the pick survives switching leagues. Plus curated
  boards for clinical/wasteful finishers and top creators.
- **Insights** — second-order reads of the xG data: the justice table (league
  re-ranked by expected points), finishing-vs-goalkeeping luck quadrants, a
  quality-vs-volatility "chaos index", home/away venue dependence by underlying
  npxGD, shot volume vs chance quality for the top shooters, hidden buildup
  engines (xGBuildup/90 leaders with barely any goals or assists), and penalty
  dependence.
- **Best of Europe** — continental leaderboards pooling all five leagues: the
  most dangerous attackers by npxG+xA per 90 and a merged justice table by
  expected points per match (per match, since two leagues play 34 games and
  three play 38). Opens with a prominent caveat: no cross-league adjustment is
  applied, and leagues differ too much for the comparison to be fair — it's a
  conversation starter, not a verdict. The league switcher hides on this tab.

Every chart and table has a collapsible **"How to read this"** explainer (what the
metric is, how it's computed, how to interpret it), and each analytics tab opens
with a collapsible **metric glossary** defining every abbreviation (xG, npxG,
xGBuildup, PPDA, deep completions, xPts…) in plain language; abbreviated column
headers also carry hover tooltips. Each tab opens with jump-chips that scroll to
its sections, and a floating back-to-top button appears once you scroll. Adapts
to light/dark mode.

## Automatic daily updates (GitHub Actions)

The site also updates itself: `.github/workflows/update.yml` runs the whole
pipeline on GitHub's servers every day at 03:15 UTC (after the evening matches),
commits `docs/` and pushes, and Pages redeploys — no local machine needed.
The database is kept in the Actions cache between runs; on a cold cache the
run starts from `seed/football-seed.sqlite.gz`, a committed snapshot of the full
local database. The seed matters because TheSportsDB is only fetched for the
current season: a database started from nothing has no past-season matches, and
in the early-August window before any ball is kicked the season scoping would
then drop every big-five league from the dashboard (this happened once —
the cloud runs briefly published an Allsvenskan-only site). A sanity check in
the workflow now refuses to publish a dashboard missing any league. To refresh
the snapshot after backfills or big local fetches:

```
python -c "import sqlite3,gzip,shutil; s=sqlite3.connect('football.sqlite'); d=sqlite3.connect('seed/_t.sqlite'); s.backup(d); d.close(); s.close(); fin=open('seed/_t.sqlite','rb'); fout=gzip.open('seed/football-seed.sqlite.gz','wb',compresslevel=9); shutil.copyfileobj(fin,fout); fout.close(); fin.close(); import os; os.unlink('seed/_t.sqlite')"
``` The run uses
`--strict`: if any fetcher fails (an API down, or FotMob blocking cloud IPs),
nothing is rebuilt or published and the site simply stays on yesterday's data.
It can also be triggered by hand from the repo's **Actions** tab ("Run
workflow"). Note: GitHub pauses scheduled workflows after ~60 days without
repository activity — one click in the Actions tab re-enables it. Running
`update.py` locally still works exactly as before and the two never conflict
(both pull --rebase before pushing).

## Web dashboard (GitHub Pages)

`docs/index.html` is a committed copy of the report, so the repo can serve it as a
live web dashboard. One-time setup on GitHub: **Settings → Pages → Deploy from a
branch → `master`, folder `/docs` → Save**. The dashboard then lives at
`https://aronalander.github.io/footytest/` and updates on every push after
re-running `build_report.py`.

## Data source

Currently [TheSportsDB](https://www.thesportsdb.com/) with the public test key (`123`),
which requires no signup but **truncates some responses** (the standings table shows
only ~5 rows; full match data is obtained via the per-round endpoint instead).
For full standings and richer stats either:

- get a personal TheSportsDB key (Patreon, ~$10/mo), or
- switch to [football-data.org](https://www.football-data.org/) (free key by email, full Serie A).

[API-Football](https://www.api-football.com/) was considered for Allsvenskan, but its
free plan turned out to be limited to **seasons 2022–2024** (verified 2026-07-16) —
no current-season standings, fixtures, or match statistics — so it can't feed this
dashboard without a paid plan (~$19/mo unlocks all seasons incl. shots on target).
API keys live in `api_keys.json` (gitignored, never committed).

League IDs used: Allsvenskan `4347` (season = calendar year), Serie A `4332` (season = `2025-2026`).

## Roadmap ideas

- [x] Store fetched data in SQLite
- [x] HTML report for viewing the data
- [x] All five big European leagues with a league switcher (Understat covers
      them all; TheSportsDB provides results for any league by ID)
- [ ] Swap in a full-data API for standings (football-data.org — API-Football's
      free plan is stuck on 2022–2024 seasons, see above)
- [x] xG analytics for Serie A via Understat (xG table, finishing boards, creators)
- [x] Compute standings trends, form tables, home/away splits
- [x] Team style profiles (PPDA pressing intensity vs deep completions)
- [x] Rolling xG-difference form curves
- [x] xG for Allsvenskan via FotMob (unofficial API — xG/xA/xGOT/SoT, player
      boards, xG table, form curves, head-to-head; Poisson-computed xPts)
- [x] Web dashboard for visualizations (GitHub Pages from `docs/`)
- [x] Hidden analytics: justice table, luck quadrants, chaos index, venue
      dependence, shot diet, hidden buildup engines, penalty dependence
- [x] Automatic season rollover (fetchers flip on 1 August; the report scopes
      to each league's current season, keeping old seasons as history)
- [x] Season archive: Understat backfill to 2014/15 and one frozen archive page
      per past season, linked from a Season dropdown
- [x] League tab on the Allsvenskan archive pages — final table, home/away
      split and all 240 results per season, derived from the FotMob rows
      already stored rather than fetched again
- [x] Club history strip: one row per season for any club, back to 2014/15,
      with the finish, the record and points against expected points — the
      first thing on the site that reads across seasons rather than within one
- [x] Career strip on the player profile card: every season Understat has
      stored for that player, across clubs and leagues, so transfers and a
      finisher's good and bad years read as one line rather than twelve pages
- [x] Search across the whole dashboard: clubs, players and sections in one
      box, opened with `/` or `Ctrl`+`K`, ranked prefix-first and folded for
      accents
- [ ] Backfill matchday results for old big-five seasons from TheSportsDB
      (works on the test key but needs ~2,000 throttled requests; would give
      those archive pages the League tab that Allsvenskan's already have)
