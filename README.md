# Football Analytics
Not serious work, just trying some good'ol vibecoding.

Analytics tool for **Serie A** (with **Allsvenskan** on hold), built on free, no-signup data sources.

Allsvenskan support is currently commented out — no free advanced-stats source exists
for it, so the project focuses on Serie A for now. To bring it back, uncomment its
entry in `fetch_data.py` and remove it from `HIDDEN_LEAGUES` in `build_report.py`.

## Quick start

Requires Python 3.10+ (standard library only, no dependencies):

```
python fetch_data.py
python fetch_understat.py
python build_report.py
```

`fetch_data.py` downloads league tables, results, and fixtures for both leagues and
stores them in `football.sqlite` (matches are upserted; standings are saved as dated
snapshots, so history accumulates the more often you run it). Matches are fetched
round by round — the test key truncates the season/recent-results endpoints but
serves complete rounds — so the whole season lands in the database. The run takes
a few minutes because the test key allows only ~30 requests/minute. Raw API
responses are also kept in `data/` for debugging.

`fetch_understat.py` pulls Serie A advanced stats from Understat's public JSON
endpoint (no signup): per-match team xG/xGA/xPts/PPDA and per-player xG/xA for
~590 players. Understat only covers the big five leagues plus Russia, so this is
the Serie A analytics layer — Allsvenskan has no free xG source.

`build_report.py` turns the database into a self-contained `report.html` — open it
in any browser (vanilla JavaScript, works offline from a double-click). It also
writes an identical copy to `docs/index.html`, which is committed so the report can
be served as a web dashboard (see below). It has four tabs:

- **League** — full standings computed from stored results (rank-trend arrows,
  W/D/L form chips), home/away split table, recent results, upcoming fixtures.
- **Team analytics** — xG table (points vs expected points), a team comparison
  block (pick 2–3 teams for a percentile radar over six style dimensions —
  attack, defence, finishing, pressing, territory, box defence — with the raw
  per-match numbers underneath, deep-linkable via `#teams=A,B,C`),
  pressing-vs-territory scatter (PPDA against deep completions), rolling
  xG-difference form curves.
- **Players** — an explorer over every tracked player (~590): search, team /
  position / minutes filters, a per-90 toggle, and click-to-sort columns for
  goals, xG, G−xG, assists, xA, shots, key passes, xGChain, xGBuildup and more.
  Click any row for a profile card with season totals and per-90 percentile bars
  vs same-position peers; a comparison block overlays up to three players on a
  percentile radar (deep-linkable via `#player=Name` / `#compare=A,B,C`). Plus
  curated boards for clinical/wasteful finishers and top creators.
- **Insights** — second-order reads of the xG data: the justice table (league
  re-ranked by expected points), finishing-vs-goalkeeping luck quadrants, a
  quality-vs-volatility "chaos index", home/away venue dependence by underlying
  npxGD, shot volume vs chance quality for the top shooters, hidden buildup
  engines (xGBuildup/90 leaders with barely any goals or assists), and penalty
  dependence.

Every chart and table has a collapsible **"How to read this"** explainer (what the
metric is, how it's computed, how to interpret it). Adapts to light/dark mode.

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
- switch to [football-data.org](https://www.football-data.org/) (free key by email, full Serie A)
  plus [API-Football](https://www.api-football.com/) (free key, 100 req/day, full Allsvenskan).

League IDs used: Allsvenskan `4347` (season = calendar year), Serie A `4332` (season = `2025-2026`).

## Roadmap ideas

- [x] Store fetched data in SQLite
- [x] HTML report for viewing the data
- [ ] Swap in full-data APIs (football-data.org + API-Football)
- [x] xG analytics for Serie A via Understat (xG table, finishing boards, creators)
- [x] Compute standings trends, form tables, home/away splits
- [x] Team style profiles (PPDA pressing intensity vs deep completions)
- [x] Rolling xG-difference form curves
- [ ] xG for Allsvenskan via FotMob (unofficial API, fragile)
- [x] Web dashboard for visualizations (GitHub Pages from `docs/`)
- [x] Hidden analytics: justice table, luck quadrants, chaos index, venue
      dependence, shot diet, hidden buildup engines, penalty dependence
