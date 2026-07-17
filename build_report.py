"""Build a self-contained HTML report from the local football.sqlite database.

Run `python fetch_data.py` (and `python fetch_understat.py`) first, then:

    python build_report.py

The result is report.html next to this script — open it in any browser.
Uses only the Python standard library; the page itself uses a little vanilla
JavaScript for tabs and the player explorer (works offline from file://).
"""

import json
import sqlite3
from datetime import date, datetime
from html import escape, unescape
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "football.sqlite"
REPORT_PATH = PROJECT_DIR / "report.html"
DOCS_PATH = PROJECT_DIR / "docs" / "index.html"  # committed copy, served by GitHub Pages

# Leagues kept in the database but left out of the report for now
HIDDEN_LEAGUES = {"Allsvenskan"}

# Preferred order of the league switcher; anything else stored comes after
LEAGUE_ORDER = ["Serie A", "Premier League", "La Liga", "Bundesliga", "Ligue 1"]

FORM_WINDOW = 5     # matches shown in the form column
TREND_WINDOW = 5    # rounds used for the rank-trend arrow
ROLLING_WINDOW = 5  # matches in the rolling xG-difference curves

CSS = """
:root {
  --surface: #f7f7f4; --card: #ffffff; --border: #e4e3df;
  --text-primary: #101010; --text-secondary: #52514e;
  --accent: #2a78d6; --accent-2: #7c5cff;
  --win: #0ca30c; --loss: #d03b3b; --draw: #8a8983;
  --row-hover: #f0f4fa; --row-alt: rgba(16,16,16,.026);
  --glow: rgba(42,120,214,.10); --glow-2: rgba(124,92,255,.08);
  --shadow: 0 1px 2px rgba(20,20,20,.05), 0 4px 16px rgba(20,20,20,.05);
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #161615; --card: #212120; --border: #3a3936;
    --text-primary: #ffffff; --text-secondary: #c3c2b7;
    --accent: #3987e5; --accent-2: #9d86ff; --draw: #75746e;
    --row-hover: #2a2b2e; --row-alt: rgba(255,255,255,.03);
    --glow: rgba(57,135,229,.17); --glow-2: rgba(157,134,255,.12);
    --shadow: 0 1px 2px rgba(0,0,0,.5), 0 4px 16px rgba(0,0,0,.35);
  }
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; padding: 28px 24px 44px; color: var(--text-primary);
  background: var(--surface);
  background-image: radial-gradient(900px 340px at 18% -80px, var(--glow), transparent 70%),
                    radial-gradient(700px 300px at 85% -120px, var(--glow-2), transparent 70%);
  background-repeat: no-repeat;
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 1240px; margin: 0 auto; }
h1 {
  font-size: 30px; letter-spacing: -0.02em; margin: 0 0 4px; width: fit-content;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  -webkit-background-clip: text; background-clip: text;
  color: transparent;
}
.badges { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0 0; }
.badge {
  font-size: 12px; font-weight: 600; color: var(--text-secondary);
  border: 1px solid var(--border); background: var(--card);
  padding: 3px 11px; border-radius: 999px;
}
h2 { font-size: 20px; letter-spacing: -0.01em; margin: 26px 0 4px; }
h3 { font-size: 13px; margin: 0; color: var(--text-primary);
     text-transform: uppercase; letter-spacing: 0.06em; }
.meta { color: var(--text-secondary); font-size: 13px; margin: 6px 0 8px; }
nav.tabs {
  display: inline-flex; gap: 2px; margin: 20px 0 4px; padding: 4px;
  position: sticky; top: 10px; z-index: 5;
  background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; box-shadow: var(--shadow);
}
nav.tabs button {
  appearance: none; background: none; border: none; border-radius: 8px;
  color: var(--text-secondary); font: inherit; font-size: 14px; font-weight: 600;
  padding: 8px 16px; cursor: pointer;
}
nav.tabs button:hover { color: var(--text-primary); background: var(--row-hover); }
nav.tabs button[aria-selected="true"] { color: #fff; background: var(--accent); }
@supports (backdrop-filter: blur(6px)) {
  nav.tabs { background: color-mix(in srgb, var(--card) 78%, transparent); backdrop-filter: blur(10px); }
}
.panel[hidden] { display: none; }
.lgview[hidden] { display: none; }
nav.lgswitch { display: flex; flex-wrap: wrap; gap: 6px; margin: 20px 0 0; }
nav.lgswitch button {
  font: inherit; font-size: 13px; font-weight: 600; color: var(--text-secondary);
  background: var(--card); border: 1px solid var(--border); border-radius: 999px;
  padding: 5px 14px; cursor: pointer;
}
nav.lgswitch button:hover { color: var(--text-primary); border-color: var(--accent); }
nav.lgswitch button[aria-selected="true"] {
  color: #fff; background: linear-gradient(90deg, var(--accent), var(--accent-2));
  border-color: transparent;
}
.tagline { margin: 2px 0 0; color: var(--text-secondary); font-size: 14.5px; }
.subnav { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0 2px; }
.subnav a {
  font-size: 12px; font-weight: 600; color: var(--text-secondary);
  border: 1px solid var(--border); background: var(--card); border-radius: 999px;
  padding: 4px 12px; cursor: pointer; user-select: none;
}
.subnav a:hover { color: var(--accent); border-color: var(--accent); }
.block { margin: 26px 0 30px; scroll-margin-top: 84px; }
.block-head {
  display: flex; gap: 8px 14px; align-items: baseline; flex-wrap: wrap;
  margin-bottom: 10px;
}
.block-head h3 { flex: 1 1 auto; }
.block-head h3::before {
  content: ""; display: inline-block; width: 9px; height: 9px; border-radius: 3px;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  margin-right: 9px; vertical-align: -1px;
}
details.about { font-size: 13px; }
details.about[open] { flex-basis: 100%; }
details.about summary {
  display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  list-style: none; user-select: none;
  font-size: 12px; font-weight: 600; color: var(--accent);
  background: var(--card); border: 1px solid var(--border);
  border-radius: 999px; padding: 3px 12px;
}
details.about summary::-webkit-details-marker { display: none; }
details.about summary::before { content: "ⓘ"; font-size: 13px; }
details.about summary:hover { border-color: var(--accent); }
.about-body {
  margin-top: 10px; padding: 12px 16px; font-size: 13.5px;
  color: var(--text-secondary); background: var(--card);
  border: 1px solid var(--border); border-left: 3px solid var(--accent);
  border-radius: 8px;
}
.about-body p { margin: 6px 0; }
.about-body strong { color: var(--text-primary); }
.card { background: var(--card); border: 1px solid var(--border);
        border-radius: 12px; overflow-x: auto; box-shadow: var(--shadow); }
.chart-card { background: var(--card); border: 1px solid var(--border);
              border-radius: 12px; padding: 14px; overflow-x: auto;
              box-shadow: var(--shadow); }
tbody tr:nth-child(even) td { background: var(--row-alt); }
tbody tr:hover td { background: var(--row-hover); }
tr.zone-cl td:first-child { box-shadow: inset 3px 0 0 var(--accent); }
tr.zone-rel td:first-child { box-shadow: inset 3px 0 0 var(--loss); }
.pos { color: var(--win); }
.neg { color: var(--loss); }
span.up { color: var(--win); }
span.down { color: var(--loss); }
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
svg text { fill: var(--text-secondary); font: 11px system-ui, sans-serif; }
svg text.pt-label { fill: var(--text-primary); }
svg .gridline { stroke: var(--border); stroke-width: 1; }
svg .zeroline { stroke: var(--text-secondary); stroke-width: 1; stroke-dasharray: 3 3; opacity: 0.6; }
svg .dot { fill: var(--accent); stroke: var(--card); stroke-width: 1.5; }
svg text.quad { font-style: italic; opacity: 0.8; }
svg .leader { stroke: var(--text-secondary); stroke-width: 1; opacity: 0.45; }
.spark-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
              gap: 14px 22px; }
.spark svg { display: block; overflow: visible; }
.spark .name { font-size: 12.5px; margin: 0 0 3px; color: var(--text-primary);
               white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.spark .rank { color: var(--text-secondary); font-weight: 600; font-size: 11px; }
.spark .val { font-size: 11.5px; font-weight: 600; color: var(--text-secondary);
              margin-left: 5px; font-variant-numeric: tabular-nums; }
.spark .val.pos { color: var(--win); }
.spark .val.neg { color: var(--loss); }
.spark-legend { font-size: 12.5px; color: var(--text-secondary); margin: 0 2px 14px; }
svg .spark-area.up { fill: var(--win); opacity: .16; }
svg .spark-area.down { fill: var(--loss); opacity: .16; }
svg .spark-line { fill: none; stroke-width: 1.8; }
svg .spark-line.up { stroke: var(--win); }
svg .spark-line.down { stroke: var(--loss); }
svg .spark-dot.up { fill: var(--win); }
svg .spark-dot.down { fill: var(--loss); }
.controls {
  display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 10px 0;
}
.controls input[type="search"], .controls input[type="number"], .controls select {
  font: inherit; font-size: 13px; color: var(--text-primary);
  background: var(--card); border: 1px solid var(--border); border-radius: 6px;
  padding: 6px 9px;
}
.controls input[type="search"] { width: 200px; }
.controls input[type="number"] { width: 84px; }
.controls input[list] { width: 180px; }
.controls label { font-size: 13px; color: var(--text-secondary); }
.controls .count { margin-left: auto; font-size: 13px; color: var(--text-secondary); }
.controls button {
  font: inherit; font-size: 13px; font-weight: 600; color: var(--accent);
  background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  padding: 6px 12px; cursor: pointer;
}
.controls button:hover { border-color: var(--accent); }
#player-table tbody tr { cursor: pointer; }
#pd-overlay {
  position: fixed; inset: 0; background: rgba(10,10,10,.55); z-index: 30;
  display: flex; align-items: flex-start; justify-content: center;
  padding: 48px 16px; overflow: auto;
}
#pd-overlay[hidden] { display: none; }
#pd-modal {
  background: var(--card); border: 1px solid var(--border); border-radius: 12px;
  box-shadow: var(--shadow); width: 100%; max-width: 560px; padding: 18px 22px 20px;
}
.pd-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.pd-head h4 { margin: 0; font-size: 19px; }
#pd-close {
  appearance: none; background: none; border: 1px solid var(--border); border-radius: 8px;
  color: var(--text-secondary); font-size: 14px; padding: 4px 10px; cursor: pointer;
}
#pd-close:hover { color: var(--text-primary); border-color: var(--accent); }
.pd-totals { display: grid; grid-template-columns: repeat(auto-fit, minmax(80px, 1fr));
             gap: 8px; margin: 14px 0 6px; }
.pd-totals > div { background: var(--surface); border: 1px solid var(--border);
                   border-radius: 8px; padding: 8px 6px; text-align: center; }
.pd-tv { display: block; font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; }
.pd-tl { font-size: 10.5px; color: var(--text-secondary); text-transform: uppercase;
         letter-spacing: 0.04em; }
.pd-row { display: flex; align-items: center; gap: 10px; margin: 7px 0; }
.pd-label { flex: 0 0 112px; font-size: 12.5px; color: var(--text-secondary); text-align: right; }
.pd-track { flex: 1; height: 10px; border-radius: 5px; background: var(--surface);
            border: 1px solid var(--border); overflow: hidden; }
.pd-fill { height: 100%; border-radius: 5px; }
.pd-fill.hi { background: var(--win); }
.pd-fill.mid { background: var(--accent); }
.pd-fill.lo { background: var(--loss); }
.pd-val { flex: 0 0 96px; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.pd-val em { color: var(--text-secondary); font-style: normal; font-size: 11px; }
#pd-compare {
  margin-top: 14px; font: inherit; font-size: 13px; font-weight: 600; color: var(--accent);
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 6px 14px; cursor: pointer;
}
#pd-compare:hover { border-color: var(--accent); }
.pc-legend { display: flex; gap: 20px; flex-wrap: wrap; margin: 4px 2px 6px; font-size: 13.5px; }
.pc-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
.pc-dot.pc0 { background: var(--accent); }
.pc-dot.pc1 { background: var(--win); }
.pc-dot.pc2 { background: var(--accent-2); }
svg .radar-grid { fill: none; stroke: var(--border); }
svg .radar-axis { stroke: var(--border); }
svg .radar-poly { fill-opacity: 0.14; stroke-width: 2; }
svg .radar-poly.pc0 { stroke: var(--accent); fill: var(--accent); }
svg .radar-poly.pc1 { stroke: var(--win); fill: var(--win); }
svg .radar-poly.pc2 { stroke: var(--accent-2); fill: var(--accent-2); }
#player-table th.sortable { cursor: pointer; user-select: none; }
#player-table th.sortable:hover { color: var(--text-primary); }
#player-table th .arrow { font-size: 10px; }
#player-table th, #player-table td { padding: 6px 8px; }
#player-table td { font-size: 13.5px; }
#player-table th[title] { cursor: help; }
#player-table th.sortable[title] { cursor: pointer; }
details.glossary {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; box-shadow: var(--shadow);
  margin: 14px 0 4px; padding: 0;
}
details.glossary summary {
  cursor: pointer; user-select: none; list-style: none;
  padding: 10px 16px; font-size: 13px; font-weight: 600; color: var(--accent);
}
details.glossary summary::-webkit-details-marker { display: none; }
details.glossary summary::before { content: "📖 "; }
details.glossary[open] summary { border-bottom: 1px solid var(--border); }
.gl-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 10px 28px; margin: 0; padding: 14px 16px 16px;
}
.gl-grid > div { font-size: 13px; }
.gl-grid dt { font-weight: 700; color: var(--text-primary); }
.gl-grid dd { margin: 1px 0 0; color: var(--text-secondary); }
.duo { display: grid; grid-template-columns: 1fr; gap: 0 24px; align-items: start; }
@media (min-width: 1000px) { .duo { grid-template-columns: 1fr 1fr; } }
.show-more { display: flex; gap: 10px; align-items: center; justify-content: center; margin: 12px 0 2px; }
.show-more button {
  font: inherit; font-size: 13px; font-weight: 600; color: var(--accent);
  background: var(--card); border: 1px solid var(--border); border-radius: 999px;
  padding: 6px 16px; cursor: pointer;
}
.show-more button:hover { border-color: var(--accent); }
#to-top {
  position: fixed; right: 22px; bottom: 22px; z-index: 20;
  width: 42px; height: 42px; border-radius: 50%;
  border: 1px solid var(--border); background: var(--card); color: var(--text-secondary);
  font-size: 18px; cursor: pointer; box-shadow: var(--shadow);
  opacity: 0; pointer-events: none; transition: opacity .2s;
}
#to-top.show { opacity: 1; pointer-events: auto; }
#to-top:hover { color: var(--accent); border-color: var(--accent); }
footer { margin-top: 32px; font-size: 13px; color: var(--text-secondary); }
"""


def fmt_delta(value, decimals=1):
    return f"{value:+.{decimals}f}".replace("-", "−")


def fmt_delta_html(value, decimals=1):
    """Signed value colored green/red; plain-text fmt_delta stays for SVG titles."""
    cls = "pos" if value > 0 else "neg" if value < 0 else "dim"
    return f"<span class='{cls}'>{fmt_delta(value, decimals)}</span>"


def block(title, body, about=None):
    """A titled section; `about` (HTML) becomes a collapsible 'How to read this'."""
    head = f"<h3>{escape(title)}</h3>"
    if about:
        head += (
            "<details class='about'><summary>How to read this</summary>"
            f"<div class='about-body'>{about}</div></details>"
        )
    return f"<section class='block'><div class='block-head'>{head}</div>{body}</section>"


def lgview(league, content, first):
    """Per-league wrapper; the league switcher toggles visibility client-side."""
    hidden = "" if first else " hidden"
    return f"<div class='lgview' data-lg='{escape(league)}'{hidden}>{content}</div>"


# ---------------------------------------------------------------- standings

def completed_matches(db, league):
    return db.execute(
        """SELECT round, match_date, home_team, away_team, home_score, away_score
           FROM matches WHERE league = ? AND home_score IS NOT NULL
           ORDER BY match_date, event_id""",
        (league,),
    ).fetchall()


def compute_table(matches, upto_round=None):
    """Standings computed from raw results; each entry also carries
    home/away sub-records. Returns rows sorted by pts, gd, gf."""
    teams = {}

    def entry(team):
        return teams.setdefault(team, {
            "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0,
            "home": {"w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0},
            "away": {"w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0},
        })

    for rnd, _, home, away, hs, as_ in matches:
        if upto_round is not None and rnd is not None and rnd > upto_round:
            continue
        for team, venue, ours, theirs in ((home, "home", hs, as_), (away, "away", as_, hs)):
            t = entry(team)
            sub = t[venue]
            t["p"] += 1
            t["gf"] += ours; t["ga"] += theirs
            sub["gf"] += ours; sub["ga"] += theirs
            outcome = "w" if ours > theirs else "l" if ours < theirs else "d"
            pts = 3 if outcome == "w" else 1 if outcome == "d" else 0
            t[outcome] += 1; t["pts"] += pts
            sub[outcome] += 1; sub["pts"] += pts

    return sorted(
        teams.items(),
        key=lambda kv: (kv[1]["pts"], kv[1]["gf"] - kv[1]["ga"], kv[1]["gf"]),
        reverse=True,
    )


def team_form(db, league, team, limit=FORM_WINDOW):
    """W/D/L letters for the team's last completed matches, oldest first."""
    rows = db.execute(
        """SELECT home_team, home_score, away_score FROM matches
           WHERE league = ? AND (home_team = ? OR away_team = ?)
             AND home_score IS NOT NULL AND away_score IS NOT NULL
           ORDER BY match_date DESC LIMIT ?""",
        (league, team, team, limit),
    ).fetchall()
    form = []
    for home, hs, as_ in rows:
        ours, theirs = (hs, as_) if home == team else (as_, hs)
        form.append("W" if ours > theirs else "L" if ours < theirs else "D")
    return list(reversed(form))


def form_chips(letters):
    return "".join(f'<span class="chip {l}">{l}</span>' for l in letters) or '<span class="dim">–</span>'


def trend_arrow(change):
    if change is None:
        return "<span class='dim'>–</span>"
    if change > 0:
        return f"<span class='up'>▲{change}</span>"
    if change < 0:
        return f"<span class='down'>▼{-change}</span>"
    return "<span class='dim'>=</span>"


def standings_table(db, league):
    matches = completed_matches(db, league)
    if not matches:
        return "<p class='dim'>No completed matches in the database yet.</p>"
    table = compute_table(matches)

    max_round = max((m[0] for m in matches if m[0] is not None), default=None)
    previous_rank = {}
    if max_round and max_round > TREND_WINDOW:
        earlier = compute_table(matches, upto_round=max_round - TREND_WINDOW)
        previous_rank = {team: i for i, (team, _) in enumerate(earlier, 1)}

    body = ""
    for rank, (team, t) in enumerate(table, 1):
        change = previous_rank[team] - rank if team in previous_rank else None
        zone = " class='zone-cl'" if rank <= 4 else " class='zone-rel'" if rank > len(table) - 3 else ""
        body += (
            f"<tr{zone}><td class='num'>{rank}</td><td>{escape(team)}</td>"
            f"<td class='num'>{t['p']}</td><td class='num'>{t['w']}</td>"
            f"<td class='num'>{t['d']}</td><td class='num'>{t['l']}</td>"
            f"<td class='num'>{t['gf']}–{t['ga']}</td>"
            f"<td class='num'>{t['gf'] - t['ga']:+d}</td>"
            f"<td class='num score'>{t['pts']}</td>"
            f"<td class='num'>{trend_arrow(change)}</td>"
            f"<td>{form_chips(team_form(db, league, team))}</td></tr>"
        )
    card = (
        "<div class='card'><table><thead><tr>"
        "<th class='num'>#</th><th>Team</th><th class='num'>P</th>"
        "<th class='num'>W</th><th class='num'>D</th><th class='num'>L</th>"
        "<th class='num'>Goals</th><th class='num'>+/−</th><th class='num'>Pts</th>"
        f"<th class='num'>±{TREND_WINDOW}R</th><th>Form</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )
    about = (
        "<p><strong>What it shows.</strong> The league table, computed from every stored "
        "result rather than copied from a website — wins are 3 points, draws 1; ties are "
        "broken by goal difference, then goals scored.</p>"
        f"<p><strong>The extras.</strong> ±{TREND_WINDOW}R is each team's change in league "
        f"position over the last {TREND_WINDOW} rounds — a quick read on who is climbing "
        f"or sliding. The form chips are the last {FORM_WINDOW} results, oldest to newest. "
        "A blue stripe marks the top four (Champions League places), a red stripe the "
        "bottom three (relegation).</p>"
    )
    return block("Standings", card, about)


def home_away_table(db, league):
    matches = completed_matches(db, league)
    if not matches:
        return ""
    body = ""
    for team, t in compute_table(matches):
        h, a = t["home"], t["away"]
        body += (
            f"<tr><td>{escape(team)}</td>"
            f"<td class='num'>{h['w']}-{h['d']}-{h['l']}</td>"
            f"<td class='num'>{h['gf']}–{h['ga']}</td><td class='num score'>{h['pts']}</td>"
            f"<td class='num'>{a['w']}-{a['d']}-{a['l']}</td>"
            f"<td class='num'>{a['gf']}–{a['ga']}</td><td class='num score'>{a['pts']}</td>"
            f"<td class='num'>{h['pts'] - a['pts']:+d}</td></tr>"
        )
    card = (
        "<div class='card'><table><thead><tr><th>Team</th>"
        "<th class='num'>Home W-D-L</th><th class='num'>Goals</th><th class='num'>Pts</th>"
        "<th class='num'>Away W-D-L</th><th class='num'>Goals</th><th class='num'>Pts</th>"
        "<th class='num'>H−A</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )
    about = (
        "<p><strong>What it shows.</strong> Each team's record split by venue, in "
        "overall-table order. H−A is home points minus away points.</p>"
        "<p><strong>How to read it.</strong> A big positive H−A is a fortress team that "
        "leans on its own ground; a value near zero is venue-proof; a negative one — rare — "
        "actually travels better than it defends home turf. Note each half is only ~19 "
        "matches, so a swing of a few points can be noise.</p>"
    )
    return block("Home / away split", card, about)


# ------------------------------------------------------------ matches lists

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


# ------------------------------------------------------- understat sections

def xg_table(db, league):
    rows = db.execute(
        """SELECT team, COUNT(*), SUM(pts), SUM(xpts), SUM(scored), SUM(missed),
                  SUM(xg), SUM(xga), SUM(npxgd)
           FROM understat_team_matches WHERE league = ?
           GROUP BY team ORDER BY SUM(pts) DESC, SUM(xpts) DESC""",
        (league,),
    ).fetchall()
    if not rows:
        return ""
    body = ""
    for rank, (team, games, pts, xpts, gf, ga, xg, xga, npxgd) in enumerate(rows, 1):
        luck = pts - xpts
        body += (
            f"<tr><td class='num'>{rank}</td><td>{escape(team)}</td>"
            f"<td class='num'>{games}</td><td class='num score'>{pts}</td>"
            f"<td class='num'>{xpts:.1f}</td><td class='num'>{fmt_delta_html(luck)}</td>"
            f"<td class='num'>{gf}–{ga}</td><td class='num'>{xg:.1f}</td>"
            f"<td class='num'>{xga:.1f}</td><td class='num'>{fmt_delta_html(npxgd)}</td></tr>"
        )
    card = (
        "<div class='card'><table><thead><tr>"
        "<th class='num'>#</th><th>Team</th><th class='num'>P</th>"
        "<th class='num'>Pts</th><th class='num'>xPts</th><th class='num'>Pts−xPts</th>"
        "<th class='num'>Goals</th><th class='num'>xG</th><th class='num'>xGA</th>"
        "<th class='num'>npxGD</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )
    about = (
        "<p><strong>What it shows.</strong> Results next to what the chances say they "
        "should have been. xG (expected goals) values every shot by how often that kind "
        "of chance is scored; xPts converts each match's shots into win/draw/loss "
        "probabilities and sums the expected points.</p>"
        "<p><strong>How to read it.</strong> Pts−xPts above zero means the team banked "
        "more points than its chances deserved — running hot on finishing, goalkeeping "
        "or timing. npxGD is non-penalty xG difference (created minus conceded), widely "
        "considered the best single number for underlying strength: it predicts future "
        "results better than points do.</p>"
        "<p><strong>Caveat.</strong> xG is a model of chance quality, not truth — elite "
        "finishers beat it consistently, and one season is a small sample.</p>"
    )
    return block("xG table — results vs expected", card, about)


def nice_ticks(lo, hi, count=5):
    span = hi - lo
    step = span / (count - 1)
    return [round(lo + i * step, 1) for i in range(count)]


def scatter_svg(points, x_label, y_label, aria, x_dec=1, y_dec=1,
                zero_x=False, zero_y=False, quadrants=None):
    """Labelled scatter plot. points = (label, x, y, hover_text).

    zero_x / zero_y draw a dashed reference line at x=0 / y=0 (and extend the
    range to include it); quadrants = (tl, tr, bl, br) corner annotations.
    """
    width, height = 860, 460
    ml, mr, mt, mb = 55, 130, 15, 45
    plot_w, plot_h = width - ml - mr, height - mt - mb

    xs = [p[1] for p in points]
    ys = [p[2] for p in points]
    xpad = (max(xs) - min(xs)) * 0.08 or 1
    ypad = (max(ys) - min(ys)) * 0.08 or 1
    x0, x1 = min(xs) - xpad, max(xs) + xpad
    y0, y1 = min(ys) - ypad, max(ys) + ypad
    if zero_x:
        x0, x1 = min(x0, -xpad), max(x1, xpad)
    if zero_y:
        y0, y1 = min(y0, -ypad), max(y1, ypad)

    def px(v):
        return ml + (v - x0) / (x1 - x0) * plot_w

    def py(v):
        return mt + (1 - (v - y0) / (y1 - y0)) * plot_h

    def fmt_tick(v, dec):
        return f"{v:.{dec}f}".replace("-", "−")

    parts = []
    for tick in nice_ticks(x0, x1):
        x = px(tick)
        parts.append(f"<line class='gridline' x1='{x:.0f}' y1='{mt}' x2='{x:.0f}' y2='{mt + plot_h}'/>")
        parts.append(f"<text x='{x:.0f}' y='{height - 24}' text-anchor='middle'>{fmt_tick(tick, x_dec)}</text>")
    for tick in nice_ticks(y0, y1):
        y = py(tick)
        parts.append(f"<line class='gridline' x1='{ml}' y1='{y:.0f}' x2='{ml + plot_w}' y2='{y:.0f}'/>")
        parts.append(f"<text x='{ml - 8}' y='{y:.0f}' text-anchor='end' dominant-baseline='middle'>{fmt_tick(tick, y_dec)}</text>")
    if zero_x:
        x = px(0)
        parts.append(f"<line class='zeroline' x1='{x:.0f}' y1='{mt}' x2='{x:.0f}' y2='{mt + plot_h}'/>")
    if zero_y:
        y = py(0)
        parts.append(f"<line class='zeroline' x1='{ml}' y1='{y:.0f}' x2='{ml + plot_w}' y2='{y:.0f}'/>")
    if quadrants:
        tl, tr, bl, br = quadrants
        for text, x, y, anchor in (
            (tl, ml + 8, mt + 16, "start"), (tr, ml + plot_w - 8, mt + 16, "end"),
            (bl, ml + 8, mt + plot_h - 8, "start"), (br, ml + plot_w - 8, mt + plot_h - 8, "end"),
        ):
            if text:
                parts.append(f"<text class='quad' x='{x}' y='{y}' text-anchor='{anchor}'>{escape(text)}</text>")
    parts.append(f"<text x='{ml + plot_w / 2:.0f}' y='{height - 6}' text-anchor='middle'>{escape(x_label)}</text>")
    parts.append(f"<text x='14' y='{mt + plot_h / 2:.0f}' text-anchor='middle' "
                 f"transform='rotate(-90 14 {mt + plot_h / 2:.0f})'>{escape(y_label)}</text>")

    # label placement: try right / left / above / below of the dot; if every
    # spot is taken, slide the label away and tie it to the dot with a leader line
    def overlaps(a, b):
        return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])

    boxes = [(px(p[1]) - 7, py(p[2]) - 7, px(p[1]) + 7, py(p[2]) + 7) for p in points]
    for label, vx, vy, hover in sorted(points, key=lambda p: (py(p[2]), px(p[1]))):
        x, y = px(vx), py(vy)
        w = 6.2 * len(label)  # rough 11px system-ui width
        candidates = (
            ("start", x + 9, y + 4, (x + 8, y - 7, x + 10 + w, y + 6)),
            ("end", x - 9, y + 4, (x - 10 - w, y - 7, x - 8, y + 6)),
            ("middle", x, y - 10, (x - w / 2, y - 21, x + w / 2, y - 8)),
            ("middle", x, y + 17, (x - w / 2, y + 7, x + w / 2, y + 20)),
        )
        chosen = None
        for anchor, tx, ty, box in candidates:
            if box[0] < 2 or box[2] > width - 2 or box[1] < mt - 2 or box[3] > mt + plot_h + 12:
                continue
            if not any(overlaps(box, b) for b in boxes):
                chosen = (anchor, tx, ty, box, None)
                break
        if chosen is None:
            # slide diagonally away from the dot until a free spot appears
            fits_right = x + 14 + w <= width - 2
            dx, anchor = (13, "start") if fits_right else (-13, "end")
            ty = y + 17
            while True:
                tx = x + dx
                box = (tx - 1, ty - 11, tx + 2 + w, ty + 2) if dx > 0 else (tx - 2 - w, ty - 11, tx + 1, ty + 2)
                if not any(overlaps(box, b) for b in boxes):
                    break
                ty += 13
            leader = (x, y, x + (11 if dx > 0 else -11), ty - 4)
            chosen = (anchor, tx, ty, box, leader)
        anchor, tx, ty, box, leader = chosen
        boxes.append(box)
        if leader:
            parts.append(f"<line class='leader' x1='{leader[0]:.0f}' y1='{leader[1]:.0f}' "
                         f"x2='{leader[2]:.0f}' y2='{leader[3]:.0f}'/>")
        parts.append(
            f"<circle class='dot' cx='{x:.0f}' cy='{y:.0f}' r='5'>"
            f"<title>{escape(hover)}</title></circle>"
        )
        parts.append(f"<text class='pt-label' x='{tx:.0f}' y='{ty:.0f}' "
                     f"text-anchor='{anchor}'>{escape(label)}</text>")

    return (
        "<div class='chart-card'>"
        f"<svg viewBox='0 0 {width} {height}' width='100%' role='img' "
        f"aria-label='{escape(aria)}'>" + "".join(parts) + "</svg></div>"
    )


def style_scatter(db, league):
    rows = db.execute(
        """SELECT team, AVG(ppda), AVG(deep) FROM understat_team_matches
           WHERE league = ? AND ppda IS NOT NULL GROUP BY team""",
        (league,),
    ).fetchall()
    if len(rows) < 2:
        return ""
    points = [
        (team, ppda, deep, f"{team}: PPDA {ppda:.1f}, deep completions {deep:.1f} per match")
        for team, ppda, deep in rows
    ]
    chart = scatter_svg(
        points,
        "PPDA — passes allowed per defensive action (left = presses harder)",
        "Deep completions per match", y_dec=0,
        aria="Scatter plot of pressing intensity against deep completions per team",
    )
    about = (
        "<p><strong>What it shows.</strong> Each team's playing identity in two numbers, "
        "averaged over the season. PPDA (passes per defensive action) counts how many "
        "passes a team lets the opponent play before making a tackle, interception or "
        "foul — fewer means a more aggressive press. Deep completions are passes "
        "completed within roughly 20 metres of the opponent's goal — a proxy for "
        "sustained territorial dominance.</p>"
        "<p><strong>How to read it.</strong> Top-left teams press high <em>and</em> pin "
        "opponents into their own box — the modern dominant style. Bottom-right teams "
        "sit deep and play direct, ceding the ball and the territory. Neither corner is "
        "'better' — it's a style map, not a quality ranking. Hover a dot for exact "
        "values.</p>"
    )
    return block("Team style — pressing vs territory", chart, about)


def rolling_sparklines(db, league):
    rows = db.execute(
        """SELECT team, npxgd FROM understat_team_matches
           WHERE league = ? ORDER BY team, match_date""",
        (league,),
    ).fetchall()
    if not rows:
        return ""
    series = {}
    for team, npxgd in rows:
        series.setdefault(team, []).append(npxgd)

    rolling = {}
    for team, values in series.items():
        if len(values) >= ROLLING_WINDOW:
            rolling[team] = [
                sum(values[i - ROLLING_WINDOW + 1:i + 1]) / ROLLING_WINDOW
                for i in range(ROLLING_WINDOW - 1, len(values))
            ]
    if not rolling:
        return ""

    max_abs = max(abs(v) for values in rolling.values() for v in values)
    order = [r[0] for r in db.execute(
        "SELECT team FROM understat_team_matches WHERE league = ? "
        "GROUP BY team ORDER BY SUM(pts) DESC", (league,)
    ) if r[0] in rolling]

    n_matches = max(len(v) for v in series.values())
    # clipPath ids must be unique across the whole page: every league's
    # sparklines coexist in the DOM, and a duplicate id resolves to the
    # (possibly hidden) first occurrence, breaking the green/red split
    lg_slug = league.lower().replace(" ", "-")
    w, h = 220, 64
    mid = h / 2
    amp = mid - 6
    cells = []
    for idx, team in enumerate(order):
        values = rolling[team]
        step = w / (len(values) - 1)
        pts = [(i * step, mid - (v / max_abs) * amp) for i, v in enumerate(values)]
        points = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        area = f"0,{mid:.1f} {points} {w},{mid:.1f}"
        last = values[-1]
        sign = "up" if last >= 0 else "down"
        val_cls = "pos" if last > 0 else "neg" if last < 0 else "dim"
        cells.append(
            f"<div class='spark'><p class='name'><span class='rank'>{idx + 1}</span> "
            f"{escape(team)}<span class='val {val_cls}'>{fmt_delta(last, 2)}</span></p>"
            f"<svg viewBox='0 0 {w} {h}' width='100%' role='img' "
            f"aria-label='{escape(team)} rolling xG difference'>"
            f"<title>{escape(team)}: rolling {ROLLING_WINDOW}-match npxGD, "
            f"season range {fmt_delta(min(values), 2)} to {fmt_delta(max(values), 2)}, "
            f"latest {fmt_delta(last, 2)}</title>"
            f"<defs>"
            f"<clipPath id='sp-{lg_slug}-{idx}t'><rect x='-4' y='-4' width='{w + 8}' height='{mid + 4}'/></clipPath>"
            f"<clipPath id='sp-{lg_slug}-{idx}b'><rect x='-4' y='{mid}' width='{w + 8}' height='{mid + 4}'/></clipPath>"
            f"</defs>"
            f"<line class='gridline' x1='{w / 2}' y1='2' x2='{w / 2}' y2='{h - 2}'/>"
            f"<polygon class='spark-area up' points='{area}' clip-path='url(#sp-{lg_slug}-{idx}t)'/>"
            f"<polygon class='spark-area down' points='{area}' clip-path='url(#sp-{lg_slug}-{idx}b)'/>"
            f"<line class='zeroline' x1='0' y1='{mid}' x2='{w}' y2='{mid}'/>"
            f"<polyline class='spark-line up' points='{points}' clip-path='url(#sp-{lg_slug}-{idx}t)'/>"
            f"<polyline class='spark-line down' points='{points}' clip-path='url(#sp-{lg_slug}-{idx}b)'/>"
            f"<circle class='spark-dot {sign}' cx='{pts[-1][0]:.1f}' cy='{pts[-1][1]:.1f}' r='3'/>"
            "</svg></div>"
        )
    legend = (
        f"<p class='spark-legend'>One panel per team, final-table order · each runs "
        f"matchday {ROLLING_WINDOW} → {n_matches}, the faint vertical line is "
        f"mid-season · <span class='pos'>green above zero</span> = out-creating "
        f"opponents, <span class='neg'>red below</span> = out-created · all panels "
        f"share the same ±{max_abs:.1f} scale · dot and number = latest "
        f"{ROLLING_WINDOW}-match window</p>"
    )
    chart = f"<div class='chart-card'>{legend}<div class='spark-grid'>{''.join(cells)}</div></div>"
    about = (
        f"<p><strong>What it shows.</strong> Every team's underlying form across the whole "
        f"season: non-penalty xG difference (chances created minus chances conceded, "
        f"penalties excluded) averaged over a rolling {ROLLING_WINDOW}-match window. Teams "
        f"appear in final-table order and all curves share the same scale "
        f"(±{max_abs:.1f}), so shapes are directly comparable.</p>"
        "<p><strong>How to read it.</strong> Green stretches above the line are periods of "
        "outplaying opponents; red dips below are periods of being outplayed. Look for the "
        "story in the shape: a title challenge that faded after mid-season, a slow starter "
        "that clicked after a coaching change, a relegated team that was actually "
        "improving. The number after each name is the latest value; hover a curve for its "
        "season range.</p>"
    )
    return block("Form curves — rolling xG difference", chart, about)


# ------------------------------------------------------------- insights tab

def justice_table(db, league):
    """League table re-ranked by expected points instead of actual points."""
    rows = db.execute(
        """SELECT team, SUM(pts), SUM(xpts) FROM understat_team_matches
           WHERE league = ? GROUP BY team""",
        (league,),
    ).fetchall()
    if not rows:
        return ""
    actual_rank = {
        team: i for i, (team, _, _) in enumerate(sorted(rows, key=lambda r: -r[1]), 1)
    }
    body = ""
    for xrank, (team, pts, xpts) in enumerate(sorted(rows, key=lambda r: -r[2]), 1):
        moved = xrank - actual_rank[team]  # >0: finished above what chances deserved
        zone = " class='zone-cl'" if xrank <= 4 else " class='zone-rel'" if xrank > len(rows) - 3 else ""
        body += (
            f"<tr{zone}><td class='num'>{xrank}</td><td>{escape(team)}</td>"
            f"<td class='num score'>{xpts:.1f}</td><td class='num'>{pts}</td>"
            f"<td class='num'>{actual_rank[team]}</td>"
            f"<td class='num'>{trend_arrow(moved)}</td></tr>"
        )
    card = (
        "<div class='card'><table><thead><tr>"
        "<th class='num'>xPts rank</th><th>Team</th><th class='num'>xPts</th>"
        "<th class='num'>Actual pts</th><th class='num'>Actual rank</th>"
        "<th class='num'>Fortune</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )
    about = (
        "<p><strong>What it shows.</strong> The league re-ranked by expected points. "
        "xPts turns each match's chances into win/draw/loss probabilities and sums the "
        "expected points — so this is the table with finishing luck, deflections and "
        "goalkeeping heroics stripped out. Stripes mark where the Champions League and "
        "relegation places <em>would</em> have gone.</p>"
        "<p><strong>How to read it.</strong> ▲ in the Fortune column means the team "
        "finished that many places <em>higher</em> in the real table than its chances "
        "deserved — a fortunate season likely to regress. ▼ means the table undersold "
        "them; those teams are the classic bounce-back picks for next season, and where "
        "the value hides in pre-season betting markets and predictions.</p>"
    )
    return block("The justice table — where the chances say you belonged", card, about)


def fortune_scatter(db, league):
    """Season over/underperformance split into finishing and goalkeeping/defence."""
    rows = db.execute(
        """SELECT team, SUM(scored) - SUM(xg), SUM(xga) - SUM(missed)
           FROM understat_team_matches WHERE league = ? GROUP BY team""",
        (league,),
    ).fetchall()
    if len(rows) < 2:
        return ""
    points = [
        (team, atk, dfn,
         f"{team}: scored {fmt_delta(atk)} goals vs xG, "
         f"conceded {fmt_delta(dfn)} fewer than xGA")
        for team, atk, dfn in rows
    ]
    chart = scatter_svg(
        points,
        "Goals scored minus xG (right = clinical finishing)",
        "xGA minus goals conceded (up = defence beat the model)",
        aria="Scatter of attacking and defensive over/underperformance per team",
        x_dec=0, y_dec=0, zero_x=True, zero_y=True,
        quadrants=("Wasteful attack, heroic defence", "Hot at both ends",
                   "Cold at both ends", "Clinical attack, leaky defence"),
    )
    about = (
        "<p><strong>What it shows.</strong> Every team's season luck, split into its two "
        "ingredients. The horizontal axis is finishing: goals scored minus the xG of the "
        "chances taken. The vertical axis is the defensive mirror: the xG of chances "
        "faced minus goals actually conceded — beating it means the keeper and defenders "
        "repelled more than the model expected.</p>"
        "<p><strong>How to read it.</strong> The dashed lines are 'exactly as expected'. "
        "A team deep in the top-right corner won its points with hot finishing <em>and</em> "
        "heroic goalkeeping at once — a combination that history says doesn't repeat. "
        "A bottom-left team was punished at both ends and is almost certainly better "
        "than its results. The interesting cases are the off-diagonal ones: a clinical "
        "attack can mask a genuinely leaky defence in the goal-difference column, and "
        "this chart un-masks it.</p>"
    )
    return block("Where the luck lived — finishing vs goalkeeping", chart, about)


def chaos_scatter(db, league):
    """Underlying quality vs match-to-match volatility."""
    rows = db.execute(
        "SELECT team, npxgd FROM understat_team_matches WHERE league = ? ORDER BY team",
        (league,),
    ).fetchall()
    series = {}
    for team, npxgd in rows:
        series.setdefault(team, []).append(npxgd)
    if len(series) < 2:
        return ""
    points = []
    for team, values in series.items():
        mean = sum(values) / len(values)
        std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
        points.append((team, mean, std,
                       f"{team}: npxGD {fmt_delta(mean, 2)} per match, "
                       f"volatility (std dev) {std:.2f}"))
    chart = scatter_svg(
        points,
        "Average non-penalty xG difference per match (right = stronger)",
        "Match-to-match volatility (std dev of npxGD)",
        aria="Scatter of average xG difference against its match-to-match volatility per team",
        x_dec=1, y_dec=1, zero_x=True,
        quadrants=("Bad and unpredictable", "Strong but streaky",
                   "Consistently outplayed", "Strong and steady"),
    )
    about = (
        "<p><strong>What it shows.</strong> Two dimensions of a season that a league "
        "table can't separate: how good a team's underlying performance was (average "
        "non-penalty xG difference per match, horizontal) and how wildly it swung from "
        "week to week (its standard deviation, vertical).</p>"
        "<p><strong>How to read it.</strong> Bottom-right is the champion profile — "
        "dominant nearly every week, no drama. Top-right teams mix demolitions with "
        "inexplicable no-shows; they often underachieve their talent because football "
        "caps a rout at 3 points. Bottom-left teams are steadily, reliably outplayed. "
        "Top-left is the neutral's favourite: total chaos, capable of anything on any "
        "given Sunday. Volatility also hints at squad depth and tactical rigidity — "
        "thin squads and one-plan teams swing harder.</p>"
    )
    return block("The chaos index — quality vs volatility", chart, about)


def venue_split_table(db, league, limit=8):
    """Teams whose underlying performance changes most between home and away."""
    rows = db.execute(
        """SELECT team,
                  AVG(CASE WHEN home_away = 'h' THEN npxgd END),
                  AVG(CASE WHEN home_away = 'a' THEN npxgd END)
           FROM understat_team_matches WHERE league = ? GROUP BY team""",
        (league,),
    ).fetchall()
    if not rows:
        return ""
    ranked = sorted(rows, key=lambda r: r[1] - r[2], reverse=True)
    shown = ranked[:limit // 2] + ranked[-limit // 2:]
    body = ""
    for team, home, away in shown:
        body += (
            f"<tr><td>{escape(team)}</td><td class='num'>{fmt_delta_html(home, 2)}</td>"
            f"<td class='num'>{fmt_delta_html(away, 2)}</td>"
            f"<td class='num score'>{fmt_delta_html(home - away, 2)}</td></tr>"
        )
    card = (
        "<div class='card'><table><thead><tr><th>Team</th>"
        "<th class='num'>Home npxGD/match</th><th class='num'>Away npxGD/match</th>"
        "<th class='num'>Home edge</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )
    about = (
        f"<p><strong>What it shows.</strong> The {limit // 2} most home-dependent teams "
        f"and the {limit // 2} most venue-proof ones — measured by underlying performance "
        "(non-penalty xG difference per match), not results. Results split by venue mix "
        "in luck; this measures how differently a team actually <em>plays</em> at home "
        "versus away.</p>"
        "<p><strong>How to read it.</strong> A big home edge suggests a style that needs "
        "its own conditions — the crowd's energy for a press, a familiar pitch for a "
        "passing game — and makes away fixtures against them far more winnable than the "
        "table implies. A near-zero or negative edge is genuinely rare and marks a "
        "mentally robust, system-driven side. Useful for match predictions: venue matters "
        "much more for some teams than others.</p>"
    )
    return block("Venue dependence — who's a different team on the road", card, about)


def shot_diet_scatter(db, league, top_shooters=30, min_minutes=900):
    """Shot volume vs average chance quality for the league's main shooters."""
    rows = db.execute(
        """SELECT player_name, team, minutes, shots, npg, npxg FROM understat_players
           WHERE league = ? AND minutes >= ? AND shots > 0
           ORDER BY shots DESC LIMIT ?""",
        (league, min_minutes, top_shooters),
    ).fetchall()
    if len(rows) < 2:
        return ""
    points = []
    for name, team, minutes, shots, npg, npxg in rows:
        volume = shots * 90 / minutes
        quality = npxg / shots
        points.append((name, volume, quality,
                       f"{name} ({team}): {shots} shots ({volume:.1f} per 90), "
                       f"{quality:.2f} npxG per shot, {npg} non-penalty goals"))
    chart = scatter_svg(
        points,
        "Shots per 90 minutes",
        "npxG per shot (up = better chances)",
        aria="Scatter of shot volume against average chance quality per player",
        x_dec=1, y_dec=2,
        quadrants=("Poacher: rare but golden chances", "The complete diet",
                   "", "Chancer: shoots from anywhere"),
    )
    about = (
        f"<p><strong>What it shows.</strong> The league's {len(points)} highest-volume "
        f"shooters (≥{min_minutes} minutes) plotted by how often they shoot (horizontal) "
        "against the average quality of each attempt (vertical, npxG per shot — "
        "penalties excluded, since a spot-kick would poison the average).</p>"
        "<p><strong>How to read it.</strong> A shot worth 0.20 npxG is a one-in-five "
        "chance, close to goal; a 0.05 shot is a hopeful hit from distance. Top-left "
        "poachers shoot rarely but only from gold positions. Bottom-right 'chancers' "
        "rack up flashy shot counts that are worth little each — high highlight-reel "
        "value, low goal value. Top-right, high volume <em>and</em> high quality, is the "
        "elite-striker profile and the rarest spot on the chart. Hover a dot for exact "
        "numbers.</p>"
    )
    return block("Shot diet — volume vs chance quality", chart, about)


def buildup_table(db, league, limit=12, min_minutes=1800):
    """Players whose buildup involvement far outstrips their goal/assist credit."""
    rows = db.execute(
        """SELECT player_name, team, position, minutes, xg_buildup, xg_chain,
                  goals + assists
           FROM understat_players WHERE league = ? AND minutes >= ?
           ORDER BY xg_buildup * 90.0 / minutes DESC LIMIT ?""",
        (league, min_minutes, limit),
    ).fetchall()
    if not rows:
        return ""
    body = ""
    for name, team, pos, minutes, buildup, chain, ga in rows:
        body += (
            f"<tr><td>{escape(name)}</td><td class='dim'>{escape(team)}</td>"
            f"<td class='dim'>{escape(pos or '')}</td><td class='num'>{minutes}</td>"
            f"<td class='num score'>{buildup * 90 / minutes:.2f}</td>"
            f"<td class='num'>{chain * 90 / minutes:.2f}</td>"
            f"<td class='num'>{ga}</td></tr>"
        )
    card = (
        "<div class='card'><table><thead><tr>"
        "<th>Player</th><th>Team</th><th>Pos</th><th class='num'>Min</th>"
        "<th class='num'>xGBuildup/90</th><th class='num'>xGChain/90</th>"
        "<th class='num'>G+A</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )
    about = (
        "<p><strong>What it shows.</strong> When a move ends in a shot, xGChain credits "
        "the shot's xG to <em>every</em> player who touched the ball in that possession. "
        "xGBuildup is the same but excludes the shooter and the assister — leaving only "
        "the contribution that never appears in any goals or assists column. This table "
        f"ranks players (≥{min_minutes} minutes) by xGBuildup per 90 minutes.</p>"
        "<p><strong>How to read it.</strong> These are the league's under-credited "
        "attack-builders — note how many are defenders and deep midfielders with almost "
        "no G+A. A player high here is the platform their team's attack stands on; sell "
        "them and the forwards' numbers mysteriously dry up. This is exactly the kind of "
        "signal scouting departments pay for, and it's invisible in a normal stats page. "
        "The G+A column is shown precisely to highlight the gap.</p>"
    )
    return block("Hidden engines — buildup value without the headlines", card, about)


def penalty_table(db, league, limit=8):
    """Players whose goal tallies lean most on penalties."""
    rows = db.execute(
        """SELECT player_name, team, goals, goals - npg, xg - npxg
           FROM understat_players WHERE league = ? AND goals > npg
           ORDER BY goals - npg DESC, goals DESC LIMIT ?""",
        (league, limit),
    ).fetchall()
    if not rows:
        return ""
    body = ""
    for name, team, goals, pens, pen_xg in rows:
        share = pens / goals * 100
        body += (
            f"<tr><td>{escape(name)}</td><td class='dim'>{escape(team)}</td>"
            f"<td class='num score'>{pens}</td><td class='num'>{goals}</td>"
            f"<td class='num'>{share:.0f}%</td><td class='num'>{pen_xg:.1f}</td></tr>"
        )
    card = (
        "<div class='card'><table><thead><tr>"
        "<th>Player</th><th>Team</th><th class='num'>Pen goals</th>"
        "<th class='num'>Total goals</th><th class='num'>Pen share</th>"
        "<th class='num'>Pen xG</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )
    about = (
        "<p><strong>What it shows.</strong> The players whose goal tallies lean most on "
        "penalties. Pen share is the fraction of their goals scored from the spot; pen xG "
        "is the expected-goals value of those kicks.</p>"
        "<p><strong>How to read it.</strong> A penalty is converted about 76% of the time "
        "regardless of who takes it, so a scoring record built on them says more about "
        "who <em>holds the ball</em> when the referee points to the spot than about who "
        "creates goals from open play. Strip the penalties before comparing raw tallies, "
        "judging a transfer fee, or paying up at a fantasy-football auction — and "
        "remember penalty duty can vanish overnight with a squad change.</p>"
    )
    return block("Penalty merchants — goal tallies with an asterisk", card, about)


def insights_panel(db, leagues):
    def content(lg):
        return (
            justice_table(db, lg) + fortune_scatter(db, lg) + chaos_scatter(db, lg)
            + venue_split_table(db, lg) + shot_diet_scatter(db, lg)
            + buildup_table(db, lg) + penalty_table(db, lg)
        )
    views = "".join(
        lgview(lg, content(lg), i == 0) for i, lg in enumerate(leagues)
    )
    return (
        f"<h2>Insights <span class='dim'>({season_label(db)}, Understat)</span></h2>"
        "<p class='meta'>Second-order reads of the xG data: what the raw tables hide.</p>"
        + metric_glossary() + views
    )


# -------------------------------------------------------------- player tab

def finishing_rows(db, league, order, limit=8, min_minutes=900):
    return db.execute(
        f"""SELECT player_name, team, minutes, shots, goals, xg, goals - xg AS diff
            FROM understat_players WHERE league = ? AND minutes >= ?
            ORDER BY diff {order} LIMIT ?""",
        (league, min_minutes, limit),
    ).fetchall()


def player_table(rows, value_header):
    body = ""
    for name, team, minutes, shots, goals, xg, diff in rows:
        body += (
            f"<tr><td>{escape(name)}</td><td class='dim'>{escape(team)}</td>"
            f"<td class='num'>{minutes}</td><td class='num'>{shots}</td>"
            f"<td class='num'>{goals}</td><td class='num'>{xg:.1f}</td>"
            f"<td class='num score'>{fmt_delta_html(diff)}</td></tr>"
        )
    return (
        "<div class='card'><table><thead><tr>"
        "<th>Player</th><th>Team</th><th class='num'>Min</th><th class='num'>Shots</th>"
        f"<th class='num'>Goals</th><th class='num'>xG</th><th class='num'>{value_header}</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )


def creators_table(db, league, limit=8, min_minutes=900):
    rows = db.execute(
        """SELECT player_name, team, minutes, key_passes, assists, xa, assists - xa
           FROM understat_players WHERE league = ? AND minutes >= ?
           ORDER BY xa DESC LIMIT ?""",
        (league, min_minutes, limit),
    ).fetchall()
    body = ""
    for name, team, minutes, key_passes, assists, xa, diff in rows:
        body += (
            f"<tr><td>{escape(name)}</td><td class='dim'>{escape(team)}</td>"
            f"<td class='num'>{minutes}</td><td class='num'>{key_passes}</td>"
            f"<td class='num'>{assists}</td><td class='num'>{xa:.1f}</td>"
            f"<td class='num score'>{fmt_delta_html(diff)}</td></tr>"
        )
    return (
        "<div class='card'><table><thead><tr>"
        "<th>Player</th><th>Team</th><th class='num'>Min</th><th class='num'>Key passes</th>"
        "<th class='num'>Assists</th><th class='num'>xA</th><th class='num'>A−xA</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )


def load_players(db, league):
    rows = db.execute(
        """SELECT player_name, team, position, games, minutes, goals, xg,
                  assists, xa, shots, key_passes, npg, npxg, xg_chain, xg_buildup
           FROM understat_players WHERE league = ? ORDER BY xg DESC""",
        (league,),
    ).fetchall()
    return [
        {
            # Understat stores some names entity-encoded ("M&#039;Bala Nzola")
            "name": unescape(r[0]), "team": unescape(r[1]), "pos": r[2] or "", "games": r[3],
            "min": r[4], "goals": r[5], "xg": round(r[6], 2),
            "assists": r[7], "xa": round(r[8], 2), "shots": r[9], "kp": r[10],
            "npg": r[11], "npxg": round(r[12], 2),
            "chain": round(r[13], 2), "buildup": round(r[14], 2),
            "gdiff": round(r[5] - r[6], 2), "adiff": round(r[7] - r[8], 2),
        }
        for r in rows
    ]


def player_explorer(players_by_lg):
    if not any(players_by_lg.values()):
        return ""
    # team filter and datalist options are (re)built client-side per league
    payload = json.dumps(players_by_lg, ensure_ascii=False).replace("</", "<\\/")

    body = (
        "<div class='controls'>"
        "<input type='search' id='pe-search' placeholder='Search player or team…'>"
        "<select id='pe-team'><option value=''>All teams</option></select>"
        "<select id='pe-pos'><option value=''>All positions</option>"
        "<option value='G'>Goalkeepers</option><option value='D'>Defenders</option>"
        "<option value='M'>Midfielders</option><option value='F'>Forwards</option></select>"
        "<label>Min minutes <input type='number' id='pe-min' value='0' min='0' step='90'></label>"
        "<label><input type='checkbox' id='pe-per90'> per 90</label>"
        "<span class='count' id='pe-count'></span>"
        "</div>"
        "<div class='card'><table id='player-table'><thead><tr></tr></thead>"
        "<tbody></tbody></table></div>"
        "<div class='show-more' id='pe-more'></div>"
        "<div id='pd-overlay' hidden><div id='pd-modal' role='dialog' aria-modal='true'></div></div>"
        f"<script>const PLAYERS_BY_LG = {payload};</script>"
    )
    total = sum(len(v) for v in players_by_lg.values())
    about = (
        f"<p><strong>What it shows.</strong> Every player Understat tracks in the "
        f"selected league this season ({total} across the big five). The table starts "
        "with the top 25 by the current sort — use "
        "the buttons under it to load more. Search by name or club, filter by position "
        "and minutes, and click any column header to sort (click again to flip direction). "
        "<strong>Click a row</strong> to open that player's profile card, with season "
        "totals and percentile bars against players of the same position.</p>"
        "<p><strong>The columns.</strong> xG and xA are expected goals and expected "
        "assists — the value of the chances a player took or created. G−xG above zero "
        "means finishing better than the chances deserved; A−xA above zero means "
        "teammates converted the chances generously. KP is key passes (passes leading "
        "directly to a shot), npxG strips out penalties.</p>"
        "<p><strong>Per 90.</strong> The toggle converts volume stats to per-90-minute "
        "rates, which makes part-time players comparable to ever-presents — players "
        "under 270 minutes are hidden in that mode to avoid tiny-sample noise. Players "
        "transferred mid-season show both clubs, comma-separated.</p>"
    )
    return block("Player explorer", body, about)


def player_compare():
    # datalist options are built client-side per league ("Name — Team" values:
    # Chromium's dropdown displays option inner text as the primary line)
    inputs = "".join(
        f"<input list='pc-list' id='pc-{i}' placeholder='Player {i}…' autocomplete='off'>"
        for i in (1, 2, 3)
    )
    body = (
        f"<div class='controls'>{inputs}"
        "<button id='pc-clear' type='button'>Clear</button></div>"
        "<datalist id='pc-list'></datalist>"
        "<div class='chart-card' id='pc-empty'><p class='dim' style='margin:4px 2px'>"
        "Pick two or three players above (or use “Add to comparison” on a player card) "
        "to see their profiles side by side.</p></div>"
        "<div class='chart-card' id='pc-card' hidden></div>"
    )
    about = (
        "<p><strong>What it shows.</strong> Up to three players overlaid on a radar of "
        "six per-90 attacking dimensions — non-penalty expected goals (npxG: the value "
        "of the shots taken, penalties excluded), expected assists (xA: the value of "
        "the chances created), shots, key passes (passes leading to a shot), xGChain "
        "(involvement anywhere in a scoring move) and xGBuildup (build-up play only, "
        "shots and assists excluded — see the glossary at the top of this tab). "
        "Each axis is the player's <em>percentile</em> among "
        "players of the same position with 450+ minutes, so a defender isn't drowned by "
        "striker numbers; the table below gives the exact per-90 rates.</p>"
        "<p><strong>How to read it.</strong> The bigger the shape, the more complete the "
        "attacking contribution — but shape <em>profile</em> matters more than area: a "
        "pure finisher spikes toward npxG and shots, a creator toward xA and key passes, "
        "a deep engine toward xGBuildup. Comparing a striker with a full-back is fair "
        "here because each is measured against their own position group.</p>"
    )
    return block("Player comparison", body, about)


def load_teams(db, league):
    rows = db.execute(
        """SELECT team, COUNT(*), SUM(pts), SUM(xpts), SUM(npxg), SUM(npxga),
                  AVG(ppda), SUM(deep), SUM(deep_allowed),
                  SUM(scored), SUM(missed), SUM(xg), SUM(xga)
           FROM understat_team_matches WHERE league = ? GROUP BY team ORDER BY team""",
        (league,),
    ).fetchall()
    return [
        {
            "team": unescape(r[0]), "mp": r[1], "pts": r[2], "xpts": round(r[3], 1),
            "npxg": round(r[4] / r[1], 2), "npxga": round(r[5] / r[1], 2),
            "ppda": round(r[6], 1),
            "deep": round(r[7] / r[1], 1), "deep_allowed": round(r[8] / r[1], 1),
            "gpm": round(r[9] / r[1], 2), "cpm": round(r[10] / r[1], 2),
            "gdiff": round(r[9] - r[11], 1), "gadiff": round(r[12] - r[10], 1),
            "ptsdiff": round(r[2] - r[3], 1),
        }
        for r in rows
    ]


def team_compare(teams_by_lg):
    if not any(teams_by_lg.values()):
        return ""
    # select options are (re)built client-side per league
    selects = "".join(
        f"<select id='tc-{i}'><option value=''>Team {i}…</option></select>"
        for i in (1, 2, 3)
    )
    payload = json.dumps(teams_by_lg, ensure_ascii=False).replace("</", "<\\/")
    body = (
        f"<div class='controls'>{selects}"
        "<button id='tc-clear' type='button'>Clear</button></div>"
        "<div class='chart-card' id='tc-empty'><p class='dim' style='margin:4px 2px'>"
        "Pick two or three teams above to see their playing styles side by side.</p></div>"
        "<div class='chart-card' id='tc-card' hidden></div>"
        f"<script>const TEAMS_BY_LG = {payload};</script>"
    )
    about = (
        "<p><strong>What it shows.</strong> Up to three teams overlaid on a radar of six "
        "style dimensions, each expressed as the team's <em>percentile</em> among the "
        "sides in that league. <strong>Attack</strong> is non-penalty xG "
        "created per match and <strong>Defence</strong> is non-penalty xG conceded "
        "(flipped, so further out = fewer chances allowed). <strong>Finishing</strong> is "
        "goals minus xG — conversion above or below what the chances deserved. "
        "<strong>Pressing</strong> is PPDA flipped (opponent passes allowed per defensive "
        "action — fewer means a higher press). <strong>Territory</strong> is deep "
        "completions per match (passes received within ~20m of the opponent goal) and "
        "<strong>Box defence</strong> is the same thing conceded, flipped.</p>"
        "<p><strong>How to read it.</strong> The shape is the identity: a dominant "
        "pressing side bulges toward Attack–Pressing–Territory, a low-block counter team "
        "can look small here yet still win points on Finishing and Box defence. The "
        "table underneath gives the raw per-match numbers behind each axis, plus points "
        "vs expected points. Shots on target aren't in the data — Understat's team feed "
        "doesn't publish them — so chance <em>quality</em> (xG) stands in for shot "
        "accuracy.</p>"
    )
    return block("Team comparison", body, about)


EXPLORER_JS = """
(function () {  // league switcher: sets window.CUR_LG, toggles .lgview blocks
  const btns = document.querySelectorAll('nav.lgswitch button');
  if (!btns.length) {
    const v = document.querySelector('.lgview');
    window.CUR_LG = v ? v.dataset.lg : null;
    return;
  }
  window.CUR_LG = btns[0].dataset.lg;
  const m = decodeURIComponent(location.hash.slice(1)).match(/(?:^|&)lg=([^&]+)/);
  if (m) {
    const want = m[1].replace(/_/g, ' ');
    btns.forEach((b) => { if (b.dataset.lg === want) window.CUR_LG = want; });
  }
  function apply() {
    btns.forEach((b) =>
      b.setAttribute('aria-selected', b.dataset.lg === window.CUR_LG ? 'true' : 'false'));
    document.querySelectorAll('.lgview').forEach((v) => {
      v.hidden = v.dataset.lg !== window.CUR_LG;
    });
  }
  btns.forEach((b) => b.addEventListener('click', () => {
    if (b.dataset.lg === window.CUR_LG) return;
    window.CUR_LG = b.dataset.lg;
    apply();
    document.dispatchEvent(new CustomEvent('leaguechange'));
  }));
  apply();
})();

(function () {
  if (typeof PLAYERS_BY_LG === 'undefined') return;
  const COLS = [
    { key: 'name',    label: 'Player' },
    { key: 'team',    label: 'Team' },
    { key: 'pos',     label: 'Pos' },
    { key: 'min',     label: 'Min',   num: true },
    { key: 'games',   label: 'Apps',  num: true, full: 'Appearances' },
    { key: 'goals',   label: 'Goals', num: true, per90: true },
    { key: 'xg',      label: 'xG',    num: true, per90: true, dec: 1, full: 'Expected goals \\u2014 the quality of the chances taken' },
    { key: 'gdiff',   label: 'G−xG',  num: true, dec: 1, signed: true, full: 'Goals minus expected goals \\u2014 finishing above (+) or below (\\u2212) the chances' },
    { key: 'assists', label: 'A',     num: true, per90: true, full: 'Assists' },
    { key: 'xa',      label: 'xA',    num: true, per90: true, dec: 1, full: 'Expected assists \\u2014 the quality of the chances created for teammates' },
    { key: 'adiff',   label: 'A−xA',  num: true, dec: 1, signed: true, full: 'Assists minus expected assists \\u2014 teammates finished generously (+) or wastefully (\\u2212)' },
    { key: 'shots',   label: 'Shots', num: true, per90: true },
    { key: 'kp',      label: 'KP',    num: true, per90: true, full: 'Key passes \\u2014 passes leading directly to a shot' },
    { key: 'npxg',    label: 'npxG',  num: true, per90: true, dec: 1, full: 'Non-penalty expected goals \\u2014 xG with penalties stripped out' },
    { key: 'chain',   label: 'xGCh',  num: true, per90: true, dec: 1, full: 'xGChain \\u2014 xG of every attacking move the player touched' },
    { key: 'buildup', label: 'xGB',   num: true, per90: true, dec: 1, full: 'xGBuildup \\u2014 xGChain minus shots and assist passes: pure build-up play' }
  ];
  const PAGE = 25;
  const state = { sortKey: 'xg', sortDir: -1, per90: false, limit: PAGE };
  const $ = (id) => document.getElementById(id);
  const thead = document.querySelector('#player-table thead tr');
  const tbody = document.querySelector('#player-table tbody');

  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  let PLAYERS = PLAYERS_BY_LG[window.CUR_LG] || [];
  function rebuildTeams() {
    const teams = Array.from(new Set(PLAYERS.flatMap((p) => p.team.split(',')))).sort();
    $('pe-team').innerHTML = "<option value=''>All teams</option>" +
      teams.map((t) => '<option>' + esc(t) + '</option>').join('');
  }

  function value(p, col) {
    let v = p[col.key];
    if (state.per90 && col.per90) v = p.min > 0 ? v * 90 / p.min : 0;
    return v;
  }

  function display(p, col) {
    const v = value(p, col);
    if (!col.num) return esc(v);
    let dec = col.dec || 0;
    if (state.per90 && col.per90) dec = 2;
    let s = v.toFixed(dec);
    if (col.signed && v > 0) s = '+' + s;
    return s.replace('-', '−');
  }

  function buildHeader() {
    thead.innerHTML = COLS.map((col) => {
      const arrow = col.key === state.sortKey ? (state.sortDir < 0 ? ' ▾' : ' ▴') : '';
      const tip = col.full ? " title=\\"" + col.full + "\\"" : '';
      return "<th class='sortable" + (col.num ? " num" : "") + "' data-key='" + col.key +
             "'" + tip + ">" + col.label + "<span class='arrow'>" + arrow + "</span></th>";
    }).join('');
  }

  function render() {
    const q = $('pe-search').value.trim().toLowerCase();
    const team = $('pe-team').value;
    const pos = $('pe-pos').value;
    const minMinutes = Math.max(Number($('pe-min').value) || 0, state.per90 ? 270 : 0);
    const col = COLS.find((c) => c.key === state.sortKey) || COLS[6];

    const rows = PLAYERS.filter((p) =>
      (!q || p.name.toLowerCase().includes(q) || p.team.toLowerCase().includes(q)) &&
      (!team || p.team.split(',').includes(team)) &&
      (!pos || (pos === 'G' ? p.pos.includes('GK') : p.pos.includes(pos))) &&
      p.min >= minMinutes
    );
    rows.sort((a, b) => {
      const va = value(a, col), vb = value(b, col);
      const cmp = col.num ? va - vb : String(va).localeCompare(String(vb));
      return cmp * state.sortDir;
    });

    buildHeader();
    const shown = rows.slice(0, state.limit);
    tbody.innerHTML = shown.map((p) =>
      "<tr data-i='" + PLAYERS.indexOf(p) + "'>" + COLS.map((c, i) => {
        const cls = c.num ? 'num' : (i === 1 || i === 2 ? 'dim' : '');
        const strong = c.key === state.sortKey ? ' score' : '';
        return "<td class='" + cls + strong + "'>" + display(p, c) + '</td>';
      }).join('') + '</tr>'
    ).join('');
    $('pe-count').textContent = 'showing ' + shown.length + ' of ' + rows.length +
      ' matching \\u00b7 ' + PLAYERS.length + ' tracked';
    const more = $('pe-more');
    if (rows.length > shown.length) {
      more.innerHTML = "<button id='pe-more-btn' type='button'>Show 50 more</button>" +
        "<button id='pe-all-btn' type='button'>Show all " + rows.length + "</button>";
      $('pe-more-btn').onclick = () => { state.limit += 50; render(); };
      $('pe-all-btn').onclick = () => { state.limit = Infinity; render(); };
    } else if (state.limit > PAGE) {
      more.innerHTML = "<button id='pe-less-btn' type='button'>Collapse to top " + PAGE + "</button>";
      $('pe-less-btn').onclick = () => { state.limit = PAGE; render(); };
    } else {
      more.innerHTML = '';
    }
  }

  thead.addEventListener('click', (e) => {
    const th = e.target.closest('th');
    if (!th) return;
    const key = th.dataset.key;
    if (state.sortKey === key) state.sortDir *= -1;
    else { state.sortKey = key; state.sortDir = -1; }
    render();
  });
  ['pe-search', 'pe-team', 'pe-pos', 'pe-min'].forEach((id) =>
    $(id).addEventListener('input', render));
  $('pe-per90').addEventListener('change', () => {
    state.per90 = $('pe-per90').checked;
    render();
  });
  document.addEventListener('leaguechange', () => {
    PLAYERS = PLAYERS_BY_LG[window.CUR_LG] || [];
    state.limit = PAGE;
    $('pe-search').value = '';
    rebuildTeams();
    render();
  });
  rebuildTeams();
  render();
})();

(function () {
  const tabs = document.querySelectorAll('nav.tabs button');
  if (!tabs.length) return;
  function activate(name) {
    tabs.forEach((b) => b.setAttribute('aria-selected', b.dataset.panel === name ? 'true' : 'false'));
    document.querySelectorAll('.panel').forEach((p) => { p.hidden = p.id !== 'panel-' + name; });
  }
  tabs.forEach((b) => b.addEventListener('click', () => {
    activate(b.dataset.panel);
    history.replaceState(null, '', '#' + b.dataset.panel);
  }));
  const initial = decodeURIComponent(location.hash.slice(1)).split('&')
    .filter((s) => s && !s.includes('='))[0] || '';
  activate(document.getElementById('panel-' + initial) ? initial : tabs[0].dataset.panel);
})();

(function () {  // player profile cards + radar comparison
  if (typeof PLAYERS_BY_LG === 'undefined') return;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  let PLAYERS = PLAYERS_BY_LG[window.CUR_LG] || [];
  function rebuildList() {
    $('pc-list').innerHTML = PLAYERS.map((p) =>
      '<option value="' + esc(p.name) + ' \\u2014 ' + esc(p.team) + '"></option>').join('');
  }
  const per90 = (p, k) => p.min > 0 ? p[k] * 90 / p.min : 0;
  const posOf = (p) => p.pos.includes('GK') ? 'GK' : ((p.pos.match(/[DMF]/) || ['F'])[0]);
  const POS_NAME = { GK: 'goalkeepers', D: 'defenders', M: 'midfielders', F: 'forwards' };
  const MIN_PEER = 450;
  const METRICS = [
    { key: 'npxg',    label: 'npxG' },
    { key: 'goals',   label: 'Goals' },
    { key: 'shots',   label: 'Shots' },
    { key: 'xa',      label: 'xA' },
    { key: 'assists', label: 'Assists' },
    { key: 'kp',      label: 'Key passes' },
    { key: 'chain',   label: 'xGChain' },
    { key: 'buildup', label: 'xGBuildup' }
  ];
  const RADAR = [
    { key: 'npxg',    label: 'npxG' },
    { key: 'shots',   label: 'Shots' },
    { key: 'xa',      label: 'xA' },
    { key: 'kp',      label: 'Key passes' },
    { key: 'chain',   label: 'xGChain' },
    { key: 'buildup', label: 'xGBuildup' }
  ];

  function peersOf(p) {
    let peers = PLAYERS.filter((q) => q.min >= MIN_PEER && posOf(q) === posOf(p));
    if (peers.length < 10) peers = PLAYERS.filter((q) => q.min >= MIN_PEER && posOf(q) !== 'GK');
    return peers;
  }
  function percentile(p, key, peers) {
    const v = per90(p, key);
    const below = peers.filter((q) => per90(q, key) <= v).length;
    return Math.round(100 * below / peers.length);
  }
  function ord(n) {
    const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }
  const signed = (v) => (v > 0 ? '+' : '') + v.toFixed(1).replace('-', '\\u2212');
  const byName = (raw) => {
    const s = String(raw || '').trim();
    // accept both "Name" and the datalist's "Name — Team" form
    return PLAYERS.find((q) => q.name === s) ||
           PLAYERS.find((q) => q.name === s.split(' \\u2014 ')[0].trim());
  };

  /* ---- profile card ---- */
  const overlay = $('pd-overlay');
  function closeDetail() { overlay.hidden = true; }
  function openDetail(p) {
    const peers = peersOf(p);
    const bars = METRICS.map((m) => {
      const pct = percentile(p, m.key, peers);
      const cls = pct >= 70 ? 'hi' : pct >= 40 ? 'mid' : 'lo';
      return "<div class='pd-row'><span class='pd-label'>" + m.label + " /90</span>" +
        "<div class='pd-track'><div class='pd-fill " + cls + "' style='width:" + pct + "%'></div></div>" +
        "<span class='pd-val'>" + per90(p, m.key).toFixed(2) + " <em>" + ord(pct) + "</em></span></div>";
    }).join('');
    const totals = [
      ['Goals', p.goals], ['Assists', p.assists], ['Shots', p.shots],
      ['Key passes', p.kp], ['G\\u2212xG', signed(p.gdiff)], ['A\\u2212xA', signed(p.adiff)]
    ].map(([l, v]) =>
      "<div><span class='pd-tv'>" + v + "</span><span class='pd-tl'>" + l + "</span></div>"
    ).join('');
    $('pd-modal').innerHTML =
      "<div class='pd-head'><div><h4>" + esc(p.name) + "</h4>" +
      "<p class='meta'>" + esc(p.team) + " \\u00b7 " + esc(p.pos) + " \\u00b7 " +
      p.games + " apps, " + p.min + " min</p></div>" +
      "<button id='pd-close' aria-label='Close'>\\u2715</button></div>" +
      "<div class='pd-totals'>" + totals + "</div>" +
      "<p class='meta'>Season totals above; bars below are per-90 rates as percentiles vs " +
      (POS_NAME[posOf(p)] || 'players') + " with " + MIN_PEER + "+ minutes.</p>" +
      bars +
      "<button id='pd-compare' type='button'>Add to comparison</button>";
    overlay.hidden = false;
    $('pd-close').onclick = closeDetail;
    $('pd-compare').onclick = () => { addToCompare(p.name); closeDetail(); };
  }
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeDetail(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDetail(); });
  document.querySelector('#player-table tbody').addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-i]');
    if (tr) openDetail(PLAYERS[Number(tr.dataset.i)]);
  });

  /* ---- comparison radar ---- */
  function radarSvg(ps) {
    const W = 460, H = 350, cx = W / 2, cy = H / 2 + 6, R = 118, N = RADAR.length;
    const pt = (i, r) => {
      const a = -Math.PI / 2 + i * 2 * Math.PI / N;
      return (cx + r * Math.cos(a)).toFixed(1) + ',' + (cy + r * Math.sin(a)).toFixed(1);
    };
    let parts = '';
    [25, 50, 75, 100].forEach((ring) => {
      parts += "<polygon class='radar-grid' points='" +
        RADAR.map((_, i) => pt(i, R * ring / 100)).join(' ') + "'/>";
    });
    RADAR.forEach((m, i) => {
      parts += "<line class='radar-axis' x1='" + cx + "' y1='" + cy + "' x2='" +
        pt(i, R).replace(',', "' y2='") + "'/>";
      const a = -Math.PI / 2 + i * 2 * Math.PI / N;
      const lx = cx + (R + 16) * Math.cos(a), ly = cy + (R + 16) * Math.sin(a);
      const anchor = Math.abs(Math.cos(a)) < 0.3 ? 'middle' : (Math.cos(a) > 0 ? 'start' : 'end');
      parts += "<text x='" + lx.toFixed(0) + "' y='" + (ly + 4).toFixed(0) +
        "' text-anchor='" + anchor + "'>" + m.label + "</text>";
    });
    ps.forEach((p, i) => {
      const peers = peersOf(p);
      const pts = RADAR.map((m, j) => pt(j, R * percentile(p, m.key, peers) / 100)).join(' ');
      parts += "<polygon class='radar-poly pc" + i + "' points='" + pts + "'><title>" +
        esc(p.name) + "</title></polygon>";
    });
    return "<svg viewBox='0 0 " + W + " " + H + "' width='100%' style='max-width:520px;display:block;margin:0 auto' " +
      "role='img' aria-label='Radar comparison of selected players'>" + parts + "</svg>";
  }
  function compareTable(ps) {
    const head = "<tr><th>per 90 (percentile)</th>" +
      ps.map((p, i) => "<th class='num'><span class='pc-dot pc" + i + "'></span>" + esc(p.name) + "</th>").join('') + '</tr>';
    const rows = RADAR.map((m) =>
      '<tr><td>' + m.label + '</td>' + ps.map((p) => {
        const peers = peersOf(p);
        return "<td class='num'>" + per90(p, m.key).toFixed(2) +
          " <span class='dim'>(" + ord(percentile(p, m.key, peers)) + ")</span></td>";
      }).join('') + '</tr>'
    ).join('');
    const info = "<tr><td class='dim'>Team \\u00b7 pos \\u00b7 minutes</td>" + ps.map((p) =>
      "<td class='num dim'>" + esc(p.team) + " \\u00b7 " + esc(p.pos) + " \\u00b7 " + p.min + "'</td>"
    ).join('') + '</tr>';
    return "<div style='overflow-x:auto'><table>" + head + rows + info + '</table></div>';
  }
  function renderCompare() {
    const seen = new Set();
    const ps = [1, 2, 3].map((i) => byName(($('pc-' + i).value || '').trim()))
      .filter((p) => p && !seen.has(p.name) && seen.add(p.name)).slice(0, 3);
    const card = $('pc-card'), empty = $('pc-empty');
    if (ps.length < 2) { card.hidden = true; empty.hidden = false; return; }
    empty.hidden = true; card.hidden = false;
    const legend = "<div class='pc-legend'>" + ps.map((p, i) =>
      "<span><span class='pc-dot pc" + i + "'></span>" + esc(p.name) +
      " <span class='dim'>(" + (POS_NAME[posOf(p)] || '') + ")</span></span>").join('') + '</div>';
    card.innerHTML = legend + radarSvg(ps) + compareTable(ps);
  }
  function addToCompare(name) {
    const inputs = [1, 2, 3].map((i) => $('pc-' + i));
    const target = inputs.find((el) => !byName(el.value.trim())) || inputs[2];
    target.value = name;
    renderCompare();
    document.querySelector("nav.tabs button[data-panel='players']").click();
    $('pc-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  [1, 2, 3].forEach((i) => $('pc-' + i).addEventListener('input', renderCompare));
  $('pc-clear').addEventListener('click', () => {
    [1, 2, 3].forEach((i) => { $('pc-' + i).value = ''; });
    renderCompare();
  });
  document.addEventListener('leaguechange', () => {
    PLAYERS = PLAYERS_BY_LG[window.CUR_LG] || [];
    rebuildList();
    [1, 2, 3].forEach((i) => { $('pc-' + i).value = ''; });
    renderCompare();
    closeDetail();
  });
  rebuildList();

  /* ---- deep links: #player=Name and #compare=Name,Name[,Name],
         optionally prefixed with lg=League_Name& ---- */
  const hash = decodeURIComponent(location.hash.slice(1)).split('&')
    .filter((s) => !s.startsWith('lg=')).join('&');
  const showPlayersTab = () => document.querySelector("nav.tabs button[data-panel='players']").click();
  if (hash.startsWith('player=')) {
    showPlayersTab();
    const p = byName(hash.slice(7));
    if (p) openDetail(p);
  } else if (hash.startsWith('compare=')) {
    showPlayersTab();
    hash.slice(8).split(',').slice(0, 3).forEach((n, i) => { $('pc-' + (i + 1)).value = n.trim(); });
    renderCompare();
  }
})();

(function () {  // team comparison radar
  if (typeof TEAMS_BY_LG === 'undefined') return;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  let TEAMS = TEAMS_BY_LG[window.CUR_LG] || [];
  function rebuildSelects() {
    const options = TEAMS.map((t) => '<option>' + esc(t.team) + '</option>').join('');
    [1, 2, 3].forEach((i) => {
      $('tc-' + i).innerHTML = "<option value=''>Team " + i + '\\u2026</option>' + options;
    });
  }
  const RADAR = [
    { key: 'npxg',         label: 'Attack',      unit: 'npxG / match',         dec: 2 },
    { key: 'npxga',        label: 'Defence',     unit: 'npxGA / match',        dec: 2, invert: true },
    { key: 'gdiff',        label: 'Finishing',   unit: 'G \\u2212 xG (season)', dec: 1, signed: true },
    { key: 'ppda',         label: 'Pressing',    unit: 'PPDA',                 dec: 1, invert: true },
    { key: 'deep',         label: 'Territory',   unit: 'deep comp. / match',   dec: 1 },
    { key: 'deep_allowed', label: 'Box defence', unit: 'deep allowed / match', dec: 1, invert: true }
  ];
  const EXTRA = [
    { key: 'pts',     label: 'Points',                dec: 0 },
    { key: 'xpts',    label: 'Expected points',       dec: 1 },
    { key: 'ptsdiff', label: 'Pts \\u2212 xPts (luck)', dec: 1, signed: true },
    { key: 'gpm',     label: 'Goals / match',         dec: 2 },
    { key: 'cpm',     label: 'Conceded / match',      dec: 2 }
  ];
  function pct(t, m) {
    const v = t[m.key];
    const below = TEAMS.filter((q) => m.invert ? q[m.key] >= v : q[m.key] <= v).length;
    return Math.round(100 * below / TEAMS.length);
  }
  function ord(n) {
    const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }
  function fmt(t, m) {
    let s = t[m.key].toFixed(m.dec);
    if (m.signed && t[m.key] > 0) s = '+' + s;
    return s.replace('-', '\\u2212');
  }
  const byTeam = (name) => TEAMS.find((t) => t.team === String(name || '').trim());

  function radarSvg(ts) {
    const W = 460, H = 350, cx = W / 2, cy = H / 2 + 6, R = 118, N = RADAR.length;
    const pt = (i, r) => {
      const a = -Math.PI / 2 + i * 2 * Math.PI / N;
      return (cx + r * Math.cos(a)).toFixed(1) + ',' + (cy + r * Math.sin(a)).toFixed(1);
    };
    let parts = '';
    [25, 50, 75, 100].forEach((ring) => {
      parts += "<polygon class='radar-grid' points='" +
        RADAR.map((_, i) => pt(i, R * ring / 100)).join(' ') + "'/>";
    });
    RADAR.forEach((m, i) => {
      parts += "<line class='radar-axis' x1='" + cx + "' y1='" + cy + "' x2='" +
        pt(i, R).replace(',', "' y2='") + "'/>";
      const a = -Math.PI / 2 + i * 2 * Math.PI / N;
      const lx = cx + (R + 16) * Math.cos(a), ly = cy + (R + 16) * Math.sin(a);
      const anchor = Math.abs(Math.cos(a)) < 0.3 ? 'middle' : (Math.cos(a) > 0 ? 'start' : 'end');
      parts += "<text x='" + lx.toFixed(0) + "' y='" + (ly + 4).toFixed(0) +
        "' text-anchor='" + anchor + "'>" + m.label + "</text>";
    });
    ts.forEach((t, i) => {
      const pts = RADAR.map((m, j) => pt(j, R * pct(t, m) / 100)).join(' ');
      parts += "<polygon class='radar-poly pc" + i + "' points='" + pts + "'><title>" +
        esc(t.team) + "</title></polygon>";
    });
    return "<svg viewBox='0 0 " + W + " " + H + "' width='100%' style='max-width:520px;display:block;margin:0 auto' " +
      "role='img' aria-label='Radar comparison of selected teams'>" + parts + "</svg>";
  }
  function compareTable(ts) {
    const head = "<tr><th>metric (league percentile)</th>" +
      ts.map((t, i) => "<th class='num'><span class='pc-dot pc" + i + "'></span>" + esc(t.team) + "</th>").join('') + '</tr>';
    const rows = RADAR.map((m) =>
      "<tr><td>" + m.label + " <span class='dim'>\\u00b7 " + m.unit + "</span></td>" +
      ts.map((t) =>
        "<td class='num'>" + fmt(t, m) + " <span class='dim'>(" + ord(pct(t, m)) + ")</span></td>"
      ).join('') + '</tr>'
    ).join('');
    const extras = EXTRA.map((m) =>
      "<tr><td class='dim'>" + m.label + '</td>' +
      ts.map((t) => "<td class='num'>" + fmt(t, m) + '</td>').join('') + '</tr>'
    ).join('');
    return "<div style='overflow-x:auto'><table>" + head + rows + extras + '</table></div>';
  }
  function renderTC() {
    const seen = new Set();
    const ts = [1, 2, 3].map((i) => byTeam($('tc-' + i).value))
      .filter((t) => t && !seen.has(t.team) && seen.add(t.team)).slice(0, 3);
    const card = $('tc-card'), empty = $('tc-empty');
    if (ts.length < 2) { card.hidden = true; empty.hidden = false; return; }
    empty.hidden = true; card.hidden = false;
    const legend = "<div class='pc-legend'>" + ts.map((t, i) =>
      "<span><span class='pc-dot pc" + i + "'></span>" + esc(t.team) +
      " <span class='dim'>(" + t.pts + " pts)</span></span>").join('') + '</div>';
    card.innerHTML = legend + radarSvg(ts) + compareTable(ts);
  }
  [1, 2, 3].forEach((i) => $('tc-' + i).addEventListener('change', renderTC));
  $('tc-clear').addEventListener('click', () => {
    [1, 2, 3].forEach((i) => { $('tc-' + i).value = ''; });
    renderTC();
  });
  document.addEventListener('leaguechange', () => {
    TEAMS = TEAMS_BY_LG[window.CUR_LG] || [];
    rebuildSelects();
    renderTC();
  });
  rebuildSelects();

  /* deep link: #teams=Name,Name[,Name], optionally prefixed with lg=League_Name& */
  const hash = decodeURIComponent(location.hash.slice(1)).split('&')
    .filter((s) => !s.startsWith('lg=')).join('&');
  if (hash.startsWith('teams=')) {
    document.querySelector("nav.tabs button[data-panel='teams']").click();
    hash.slice(6).split(',').slice(0, 3).forEach((n, i) => { $('tc-' + (i + 1)).value = n.trim(); });
    renderTC();
  }
})();

(function () {  // per-tab section navigation chips (rebuilt on league switch)
  function build() {
    document.querySelectorAll('section.panel').forEach((panel) => {
      let nav = panel.querySelector('nav.subnav');
      const blocks = Array.from(panel.querySelectorAll('section.block')).filter((b) => {
        const view = b.closest('.lgview');
        return !view || !view.hidden;
      });
      if (blocks.length < 3) { if (nav) nav.remove(); return; }
      if (!nav) {
        nav = document.createElement('nav');
        nav.className = 'subnav';
        const h2 = panel.querySelector('h2');
        if (h2 && !h2.closest('.lgview')) h2.after(nav); else panel.prepend(nav);
      }
      nav.innerHTML = '';
      blocks.forEach((b) => {
        const h = b.querySelector('h3');
        if (!h) return;
        const a = document.createElement('a');
        a.textContent = h.textContent.split(' \\u2014 ')[0];
        a.addEventListener('click', () => b.scrollIntoView({ behavior: 'smooth', block: 'start' }));
        nav.appendChild(a);
      });
    });
  }
  build();
  document.addEventListener('leaguechange', build);
})();

(function () {  // back-to-top button
  const btn = document.createElement('button');
  btn.id = 'to-top';
  btn.title = 'Back to top';
  btn.setAttribute('aria-label', 'Back to top');
  btn.textContent = '\\u2191';
  document.body.appendChild(btn);
  btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
  window.addEventListener('scroll', () => {
    btn.classList.toggle('show', window.scrollY > 500);
  }, { passive: true });
})();
"""


# ------------------------------------------------------------------- report

def league_section(db, league):
    return (
        f"<h2>{escape(league)}</h2>"
        + standings_table(db, league)
        + home_away_table(db, league)
        + block("Recent results", matches_table(db, league, finished=True))
        + block("Upcoming fixtures", matches_table(db, league, finished=False))
    )


def understat_available(db):
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'understat%'"
    )}
    if "understat_team_matches" not in tables:
        return False
    return db.execute("SELECT COUNT(*) FROM understat_team_matches").fetchone()[0] > 0


def season_label(db):
    season = db.execute("SELECT MAX(season) FROM understat_players").fetchone()[0]
    return f"{season}/{int(season) % 100 + 1}" if season else ""


GLOSSARY = [
    ("xG — expected goals",
     "How good a team's or player's chances were. Every shot gets a value between 0 "
     "and 1: the probability that an average player scores from that spot and "
     "situation. A tap-in is ~0.9, a hopeful 30-metre hit ~0.02. Add them up and you "
     "get how many goals the chances “should” have produced."),
    ("xA — expected assists",
     "The same idea for passing: the xG of the shot a pass created. A player who "
     "keeps serving up big chances gets a high xA even if teammates keep missing "
     "them."),
    ("npxG / npg — non-penalty xG and goals",
     "xG and goals with penalties removed. A penalty is worth ~0.76 xG no matter who "
     "wins it, so stripping them out shows how much a player or team creates from "
     "open play."),
    ("G−xG — finishing",
     "Goals scored minus expected goals. Above zero: converting chances an average "
     "finisher would miss. Below zero: missing chances that usually go in. Tends to "
     "swing back toward zero over time."),
    ("KP — key passes",
     "Passes that led directly to a shot, whether or not it went in. Raw creativity "
     "volume, where xA measures the quality of what was created."),
    ("xGChain (xGCh)",
     "Credit for being anywhere in a move that ended in a shot: the full xG of the "
     "chance is credited to every player who touched the ball in the build-up. "
     "Rewards involvement, not just the final pass or shot."),
    ("xGBuildup (xGB)",
     "xGChain minus the shot and the assist pass. What's left is pure build-up play "
     "— deep-lying passers and defenders who start attacks score high here even "
     "with zero goals and assists."),
    ("PPDA — pressing intensity",
     "Opponent passes allowed per defensive action in their half. Counter-intuitive "
     "direction: a LOW number means an aggressive press (the opponent barely gets "
     "10 passes before being tackled), a high number means the team sits back."),
    ("Deep completions",
     "Passes received within roughly 20 metres of the opponent's goal (crosses "
     "excluded). A good measure of sustained territory and box presence."),
    ("xPts — expected points",
     "How many points a match “should” have given based on both teams' chances: "
     "the chance quality is converted into win/draw/loss probabilities and summed "
     "over the season. A team far above its xPts has been winning tight or lucky "
     "games."),
    ("npxGD — underlying dominance",
     "Non-penalty xG created minus non-penalty xG conceded, per match. The single "
     "best summary of how well a team actually played, ignoring finishing luck at "
     "both ends."),
]


def metric_glossary():
    items = "".join(
        f"<div><dt>{term}</dt><dd>{definition}</dd></div>"
        for term, definition in GLOSSARY
    )
    return (
        "<details class='glossary'><summary>Metric glossary — what xG, npxG, "
        "xGBuildup, PPDA and friends actually mean</summary>"
        f"<dl class='gl-grid'>{items}</dl></details>"
    )


def teams_panel(db, leagues):
    tables = "".join(
        lgview(lg, xg_table(db, lg), i == 0) for i, lg in enumerate(leagues)
    )
    charts = "".join(
        lgview(lg, style_scatter(db, lg) + rolling_sparklines(db, lg), i == 0)
        for i, lg in enumerate(leagues)
    )
    return (
        f"<h2>Team analytics <span class='dim'>({season_label(db)}, Understat)</span></h2>"
        + metric_glossary() + tables
        + team_compare({lg: load_teams(db, lg) for lg in leagues})
        + charts
    )


def players_panel(db, leagues):
    finishing_about = (
        "<p><strong>What it shows.</strong> The players (≥900 minutes) whose goal tallies "
        "differ most from the value of their chances. G−xG is goals scored minus expected "
        "goals: far above zero means converting chances an average finisher would miss.</p>"
        "<p><strong>How to read it.</strong> A single hot season can be luck; players who "
        "beat their xG year after year are genuinely elite finishers. Check the shots "
        "column too — a big overshoot on few shots is far flukier than the same overshoot "
        "on a hundred.</p>"
    )
    wasteful_about = (
        "<p><strong>What it shows.</strong> The other end of the list — players "
        "(≥900 minutes) who scored the fewest goals relative to the chances they had.</p>"
        "<p><strong>How to read it.</strong> This is not simply a wall of shame: a player "
        "here with a high xG is still <em>getting into</em> great positions, which is the "
        "hard part — finishing tends to bounce back. A player with low xG <em>and</em> a "
        "big negative gap has a real problem.</p>"
    )
    creators_about = (
        "<p><strong>What it shows.</strong> The league's best chance creators "
        "(≥900 minutes), ranked by expected assists — the probability that the shots "
        "their passes created would be scored.</p>"
        "<p><strong>How to read it.</strong> xA measures the quality of the chance "
        "served, independent of whether the teammate buried it. A−xA below zero means "
        "the creator was let down by finishing; above zero means teammates converted "
        "generously. xA is the fairer ranking of creativity than raw assists.</p>"
    )
    def boards(lg):
        return (
            "<div class='duo'>"
            + block("Clinical finishers — most goals above xG",
                    player_table(finishing_rows(db, lg, "DESC"), "G−xG"), finishing_about)
            + block("Wasteful in front of goal — most goals below xG",
                    player_table(finishing_rows(db, lg, "ASC"), "G−xG"), wasteful_about)
            + "</div>"
            + block("Top creators by expected assists", creators_table(db, lg), creators_about)
        )
    players_by_lg = {lg: load_players(db, lg) for lg in leagues}
    views = "".join(lgview(lg, boards(lg), i == 0) for i, lg in enumerate(leagues))
    return (
        f"<h2>Players <span class='dim'>({season_label(db)}, Understat)</span></h2>"
        + metric_glossary()
        + player_explorer(players_by_lg)
        + player_compare()
        + views
    )


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit("No football.sqlite found - run `python fetch_data.py` first.")
    db = sqlite3.connect(DB_PATH)
    stored = [
        r[0] for r in db.execute("SELECT DISTINCT league FROM matches ORDER BY league")
        if r[0] not in HIDDEN_LEAGUES
    ]
    leagues = [lg for lg in LEAGUE_ORDER if lg in stored]
    leagues += [lg for lg in stored if lg not in leagues]
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    panels = [("league", "League", "".join(
        lgview(lg, league_section(db, lg), i == 0) for i, lg in enumerate(leagues)
    ))]
    if understat_available(db):
        panels.append(("teams", "Team analytics", teams_panel(db, leagues)))
        panels.append(("players", "Players", players_panel(db, leagues)))
        panels.append(("insights", "Insights", insights_panel(db, leagues)))

    lg_bar = ""
    if len(leagues) > 1:
        lg_bar = "<nav class='lgswitch'>" + "".join(
            f"<button data-lg='{escape(lg)}' aria-selected='{'true' if i == 0 else 'false'}'>{escape(lg)}</button>"
            for i, lg in enumerate(leagues)
        ) + "</nav>"
    tab_bar = ""
    if len(panels) > 1:
        tab_bar = "<nav class='tabs'>" + "".join(
            f"<button data-panel='{pid}' aria-selected='{'true' if i == 0 else 'false'}'>{title}</button>"
            for i, (pid, title, _) in enumerate(panels)
        ) + "</nav>"
    panel_html = "".join(
        f"<section class='panel' id='panel-{pid}'{'' if i == 0 else ' hidden'}>{content}</section>"
        for i, (pid, _, content) in enumerate(panels)
    )

    badges = "".join(
        f"<span class='badge'>{escape(text)}</span>"
        for text in ([f"Big five leagues {season_label(db)}".strip()]
                     + ["TheSportsDB + Understat", f"Generated {generated}"])
    )
    html = (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Football dashboard</title><style>{CSS}</style></head><body><div class='wrap'>"
        f"<header class='hero'><h1>Football dashboard</h1>"
        f"<p class='tagline'>The big five European leagues under the hood — standings, "
        f"xG team analytics, player profiles and second-order insights.</p>"
        f"<div class='badges'>{badges}</div></header>"
        + lg_bar + tab_bar + panel_html
        + "<footer>Standings are computed from the stored results. Run "
        "<code>python fetch_data.py</code> and <code>python fetch_understat.py</code> "
        "regularly to keep the database current.</footer></div>"
        f"<script>{EXPLORER_JS}</script></body></html>"
    )
    REPORT_PATH.write_text(html, encoding="utf-8")
    DOCS_PATH.parent.mkdir(exist_ok=True)
    DOCS_PATH.write_text(html, encoding="utf-8")
    db.close()
    print(f"Report written to {REPORT_PATH}")
    print(f"Dashboard copy written to {DOCS_PATH} (commit it and it's served by GitHub Pages)")


if __name__ == "__main__":
    main()
