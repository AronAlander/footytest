"""Build a self-contained HTML report from the local football.sqlite database.

Run `python fetch_data.py` first, then:

    python build_report.py

The result is report.html next to this script — open it in any browser.
Uses only the Python standard library.
"""

import sqlite3
from datetime import date, datetime
from html import escape
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "football.sqlite"
REPORT_PATH = PROJECT_DIR / "report.html"

CSS = """
:root {
  --surface: #fcfcfb; --card: #ffffff; --border: #e4e3df;
  --text-primary: #0b0b0b; --text-secondary: #52514e;
  --win: #0ca30c; --loss: #d03b3b; --draw: #8a8983;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #1a1a19; --card: #232322; --border: #3a3936;
    --text-primary: #ffffff; --text-secondary: #c3c2b7;
    --draw: #75746e;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px; background: var(--surface); color: var(--text-primary);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 900px; margin: 0 auto; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 19px; margin: 32px 0 12px; }
h3 { font-size: 14px; margin: 20px 0 8px; color: var(--text-secondary);
     text-transform: uppercase; letter-spacing: 0.05em; }
.meta { color: var(--text-secondary); font-size: 13px; margin-bottom: 8px; }
.card { background: var(--card); border: 1px solid var(--border);
        border-radius: 8px; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: 7px 12px; text-align: left; white-space: nowrap; }
th { font-size: 12px; color: var(--text-secondary); text-transform: uppercase;
     letter-spacing: 0.04em; border-bottom: 1px solid var(--border); }
td { border-bottom: 1px solid var(--border); }
tr:last-child td { border-bottom: none; }
td.num, th.num { text-align: right; }
.score { font-weight: 600; }
.dim { color: var(--text-secondary); }
.chip {
  display: inline-block; width: 20px; height: 20px; line-height: 20px;
  border-radius: 4px; color: #fff; font-size: 11px; font-weight: 700;
  text-align: center; margin-right: 2px;
}
.chip.W { background: var(--win); }
.chip.L { background: var(--loss); }
.chip.D { background: var(--draw); }
footer { margin-top: 32px; font-size: 13px; color: var(--text-secondary); }
"""


def team_form(db, league, team, limit=5):
    """W/D/L letters for the team's last completed matches, oldest first."""
    rows = db.execute(
        """SELECT home_team, away_team, home_score, away_score FROM matches
           WHERE league = ? AND (home_team = ? OR away_team = ?)
             AND home_score IS NOT NULL AND away_score IS NOT NULL
           ORDER BY match_date DESC LIMIT ?""",
        (league, team, team, limit),
    ).fetchall()
    form = []
    for home, away, hs, as_ in rows:
        ours, theirs = (hs, as_) if home == team else (as_, hs)
        form.append("W" if ours > theirs else "L" if ours < theirs else "D")
    return list(reversed(form))


def form_chips(letters):
    return "".join(f'<span class="chip {l}">{l}</span>' for l in letters) or '<span class="dim">–</span>'


def standings_table(db, league):
    snapshot = db.execute(
        "SELECT MAX(snapshot_date) FROM standings WHERE league = ?", (league,)
    ).fetchone()[0]
    if not snapshot:
        return "<p class='dim'>No standings data yet.</p>"
    rows = db.execute(
        """SELECT rank, team, played, wins, draws, losses,
                  goals_for, goals_against, goal_diff, points
           FROM standings WHERE league = ? AND snapshot_date = ? ORDER BY rank""",
        (league, snapshot),
    ).fetchall()
    body = ""
    for rank, team, p, w, d, l, gf, ga, gd, pts in rows:
        chips = form_chips(team_form(db, league, team))
        body += (
            f"<tr><td class='num'>{rank}</td><td>{escape(team)}</td>"
            f"<td class='num'>{p}</td><td class='num'>{w}</td>"
            f"<td class='num'>{d}</td><td class='num'>{l}</td>"
            f"<td class='num'>{gf}–{ga}</td><td class='num'>{gd:+d}</td>"
            f"<td class='num score'>{pts}</td><td>{chips}</td></tr>"
        )
    return (
        "<div class='card'><table><thead><tr>"
        "<th class='num'>#</th><th>Team</th><th class='num'>P</th>"
        "<th class='num'>W</th><th class='num'>D</th><th class='num'>L</th>"
        "<th class='num'>Goals</th><th class='num'>+/−</th>"
        "<th class='num'>Pts</th><th>Form</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
        f"<p class='meta'>Standings snapshot from {snapshot}.</p>"
    )


def matches_table(db, league, finished, limit=10):
    if finished:
        rows = db.execute(
            """SELECT match_date, round, home_team, home_score, away_score, away_team
               FROM matches WHERE league = ? AND home_score IS NOT NULL
               ORDER BY match_date DESC, event_id LIMIT ?""",
            (league, limit),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT match_date, round, home_team, home_score, away_score, away_team
               FROM matches WHERE league = ? AND home_score IS NULL AND match_date >= ?
               ORDER BY match_date, event_id LIMIT ?""",
            (league, date.today().isoformat(), limit),
        ).fetchall()
    if not rows:
        return "<p class='dim'>No matches in the database yet.</p>"
    body = ""
    for match_date, rnd, home, hs, as_, away in rows:
        score = f"<span class='score'>{hs} – {as_}</span>" if hs is not None else "<span class='dim'>vs</span>"
        rnd_label = f"R{rnd}" if rnd else ""
        body += (
            f"<tr><td class='dim'>{escape(match_date or '')}</td><td class='dim'>{rnd_label}</td>"
            f"<td style='text-align:right'>{escape(home or '')}</td><td style='text-align:center'>{score}</td>"
            f"<td>{escape(away or '')}</td></tr>"
        )
    return f"<div class='card'><table><tbody>{body}</tbody></table></div>"


def league_section(db, league):
    return (
        f"<h2>{escape(league)}</h2>"
        "<h3>Standings</h3>" + standings_table(db, league) +
        "<h3>Recent results</h3>" + matches_table(db, league, finished=True) +
        "<h3>Upcoming fixtures</h3>" + matches_table(db, league, finished=False)
    )


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit("No football.sqlite found - run `python fetch_data.py` first.")
    db = sqlite3.connect(DB_PATH)
    leagues = [r[0] for r in db.execute("SELECT DISTINCT league FROM matches ORDER BY league")]
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Football report</title><style>{CSS}</style></head><body><div class='wrap'>"
        f"<h1>Football report</h1>"
        f"<p class='meta'>Generated {generated} · data from TheSportsDB (test key, truncated coverage)</p>"
        + "".join(league_section(db, league) for league in leagues)
        + "<footer>Form column shows the last completed matches known to the local database, "
        "oldest to newest (W = win, D = draw, L = loss). Run <code>python fetch_data.py</code> "
        "regularly to accumulate more history.</footer></div></body></html>"
    )
    REPORT_PATH.write_text(html, encoding="utf-8")
    db.close()
    print(f"Report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
