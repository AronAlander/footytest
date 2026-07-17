# Football Analytics
Not serious work, just trying some good'ol vibecoding.

Analytics tool for the **big five European leagues** — Serie A, Premier League,
La Liga, Bundesliga and Ligue 1 — built on free, no-signup data sources. The
dashboard has a league switcher; every tab (standings, team analytics, players,
insights) works for all five leagues.

Allsvenskan support is on hold — no free advanced-stats source exists for it. To
bring back its basic data, uncomment its entry in `fetch_data.py` and remove it
from `HIDDEN_LEAGUES` in `build_report.py`.

## Quick start

Requires Python 3.10+ (standard library only, no dependencies):

```
python fetch_data.py
python fetch_understat.py
python build_report.py
```

The season is picked automatically: both fetchers flip to the new campaign on
1 August (European seasons run autumn–spring), and the report scopes every table
and chart to each league's current season — older seasons stay in the database
as history but never mix into the dashboard. No annual maintenance needed.

Older seasons are browsable too: a **Season** dropdown in the header links to one
frozen archive page per past season (`docs/archive/2014-15.html` …). Understat's
history goes back to 2014/15, and `python fetch_understat.py --backfill` pulls
all of it (~55 requests, a couple of minutes). Archive pages carry the four
Understat tabs — team analytics, players, insights, Best of Europe — including
the team head-to-head deep dive; matchday results and standings snapshots exist
for the current season only (backfilling them from TheSportsDB would take
~2,000 throttled requests, an hour+ — skipped for now).

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
the big five leagues plus Russia — which is why Allsvenskan (no free xG source)
stays on hold.

`build_report.py` turns the database into a self-contained `report.html` — open it
in any browser (vanilla JavaScript, works offline from a double-click). It also
writes an identical copy to `docs/index.html`, which is committed so the report can
be served as a web dashboard (see below). A league switcher at the top flips the
whole dashboard between the five leagues (deep-linkable by prefixing any link with
`#lg=Premier_League&…`); below it are five tabs:

- **League** — full standings computed from stored results (rank-trend arrows,
  W/D/L form chips), home/away split table, recent results, upcoming fixtures.
- **Team analytics** — xG table (points vs expected points), a team comparison
  block (pick 2–3 teams for a percentile radar over six style dimensions —
  attack, defence, finishing, pressing, territory, box defence — with the raw
  per-match numbers underneath, deep-linkable via `#teams=A,B,C`),
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
  percentile radar (deep-linkable via `#player=Name` / `#compare=A,B,C`). The
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
- [ ] xG for Allsvenskan via FotMob (unofficial API, fragile)
- [x] Web dashboard for visualizations (GitHub Pages from `docs/`)
- [x] Hidden analytics: justice table, luck quadrants, chaos index, venue
      dependence, shot diet, hidden buildup engines, penalty dependence
- [x] Automatic season rollover (fetchers flip on 1 August; the report scopes
      to each league's current season, keeping old seasons as history)
- [x] Season archive: Understat backfill to 2014/15 and one frozen archive page
      per past season, linked from a Season dropdown
- [ ] Backfill matchday results for old seasons from TheSportsDB (works on the
      test key but needs ~2,000 throttled requests; would enable the League tab
      and meetings-by-round on archive pages)
