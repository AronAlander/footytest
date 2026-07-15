# Football Analytics

Analytics tool for **Allsvenskan** and **Serie A**, starting from free, no-signup data sources.

## Quick start

Requires Python 3.10+ (standard library only, no dependencies):

```
python fetch_data.py
python build_report.py
```

`fetch_data.py` downloads league tables, results, and fixtures for both leagues and
stores them in `football.sqlite` (matches are upserted; standings are saved as dated
snapshots, so history accumulates the more often you run it). Matches are fetched
round by round — the test key truncates the season/recent-results endpoints but
serves complete rounds — so the whole season lands in the database. The run takes
a few minutes because the test key allows only ~30 requests/minute. Raw API
responses are also kept in `data/` for debugging.

`build_report.py` turns the database into a self-contained `report.html` — open it in
any browser. It shows standings (with a W/D/L form column), recent results, and
upcoming fixtures per league, and adapts to light/dark mode.

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
- [ ] Compute standings trends, form tables, home/away splits
- [ ] xG data via Understat (Serie A) / FotMob (Allsvenskan) scraping
- [ ] Web dashboard for visualizations
