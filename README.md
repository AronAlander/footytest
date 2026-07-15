# Football Analytics

Analytics tool for **Allsvenskan** and **Serie A**, starting from free, no-signup data sources.

## Quick start

Requires Python 3.10+ (standard library only, no dependencies):

```
python fetch_data.py
```

This downloads league tables, recent results, upcoming fixtures, and season schedules
for both leagues into `data/` as raw JSON.

## Data source

Currently [TheSportsDB](https://www.thesportsdb.com/) with the public test key (`123`),
which requires no signup but **truncates responses** (~5 table rows, ~15 events per call).
Good enough for developing the pipeline; for full data either:

- get a personal TheSportsDB key (Patreon, ~$10/mo), or
- switch to [football-data.org](https://www.football-data.org/) (free key by email, full Serie A)
  plus [API-Football](https://www.api-football.com/) (free key, 100 req/day, full Allsvenskan).

League IDs used: Allsvenskan `4347` (season = calendar year), Serie A `4332` (season = `2025-2026`).

## Roadmap ideas

- [ ] Store fetched data in SQLite instead of raw JSON
- [ ] Swap in full-data APIs (football-data.org + API-Football)
- [ ] Compute standings trends, form tables, home/away splits
- [ ] xG data via Understat (Serie A) / FotMob (Allsvenskan) scraping
- [ ] Web dashboard for visualizations
