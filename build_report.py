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
from html import escape
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "football.sqlite"
REPORT_PATH = PROJECT_DIR / "report.html"

# Leagues kept in the database but left out of the report for now
HIDDEN_LEAGUES = {"Allsvenskan"}

FORM_WINDOW = 5     # matches shown in the form column
TREND_WINDOW = 5    # rounds used for the rank-trend arrow
ROLLING_WINDOW = 5  # matches in the rolling xG-difference curves

CSS = """
:root {
  --surface: #fcfcfb; --card: #ffffff; --border: #e4e3df;
  --text-primary: #0b0b0b; --text-secondary: #52514e;
  --accent: #2a78d6; --win: #0ca30c; --loss: #d03b3b; --draw: #8a8983;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #1a1a19; --card: #232322; --border: #3a3936;
    --text-primary: #ffffff; --text-secondary: #c3c2b7;
    --accent: #3987e5; --draw: #75746e;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px; background: var(--surface); color: var(--text-primary);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 960px; margin: 0 auto; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 19px; margin: 28px 0 12px; }
h3 { font-size: 14px; margin: 20px 0 8px; color: var(--text-secondary);
     text-transform: uppercase; letter-spacing: 0.05em; }
.meta { color: var(--text-secondary); font-size: 13px; margin: 6px 0 8px; }
nav.tabs {
  display: flex; gap: 4px; margin: 18px 0 4px; border-bottom: 1px solid var(--border);
  position: sticky; top: 0; background: var(--surface); z-index: 5; padding-top: 6px;
}
nav.tabs button {
  appearance: none; background: none; border: none; border-bottom: 2px solid transparent;
  color: var(--text-secondary); font: inherit; font-size: 14px; font-weight: 600;
  padding: 8px 14px; cursor: pointer;
}
nav.tabs button:hover { color: var(--text-primary); }
nav.tabs button[aria-selected="true"] {
  color: var(--text-primary); border-bottom-color: var(--accent);
}
.panel[hidden] { display: none; }
.card { background: var(--card); border: 1px solid var(--border);
        border-radius: 8px; overflow-x: auto; }
.chart-card { background: var(--card); border: 1px solid var(--border);
              border-radius: 8px; padding: 14px; overflow-x: auto; }
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
svg .dot { fill: var(--accent); }
svg .curve { stroke: var(--accent); stroke-width: 2; fill: none; }
.spark-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(158px, 1fr));
              gap: 12px 18px; }
.spark .name { font-size: 12px; margin: 0 0 2px; color: var(--text-primary);
               white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.spark .val { font-size: 11px; color: var(--text-secondary); margin-left: 4px; }
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
.controls label { font-size: 13px; color: var(--text-secondary); }
.controls .count { margin-left: auto; font-size: 13px; color: var(--text-secondary); }
#player-table th.sortable { cursor: pointer; user-select: none; }
#player-table th.sortable:hover { color: var(--text-primary); }
#player-table th .arrow { font-size: 10px; }
footer { margin-top: 32px; font-size: 13px; color: var(--text-secondary); }
"""


def fmt_delta(value, decimals=1):
    return f"{value:+.{decimals}f}".replace("-", "−")


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
        return f"▲{change}"
    if change < 0:
        return f"▼{-change}"
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
        body += (
            f"<tr><td class='num'>{rank}</td><td>{escape(team)}</td>"
            f"<td class='num'>{t['p']}</td><td class='num'>{t['w']}</td>"
            f"<td class='num'>{t['d']}</td><td class='num'>{t['l']}</td>"
            f"<td class='num'>{t['gf']}–{t['ga']}</td>"
            f"<td class='num'>{t['gf'] - t['ga']:+d}</td>"
            f"<td class='num score'>{t['pts']}</td>"
            f"<td class='num'>{trend_arrow(change)}</td>"
            f"<td>{form_chips(team_form(db, league, team))}</td></tr>"
        )
    return (
        "<div class='card'><table><thead><tr>"
        "<th class='num'>#</th><th>Team</th><th class='num'>P</th>"
        "<th class='num'>W</th><th class='num'>D</th><th class='num'>L</th>"
        "<th class='num'>Goals</th><th class='num'>+/−</th><th class='num'>Pts</th>"
        f"<th class='num'>±{TREND_WINDOW}R</th><th>Form</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
        f"<p class='meta'>Computed from stored results. ±{TREND_WINDOW}R is the change in "
        f"league position over the last {TREND_WINDOW} rounds; form shows the last "
        f"{FORM_WINDOW} matches, oldest to newest.</p>"
    )


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
    return (
        "<h3>Home / away split</h3>"
        "<div class='card'><table><thead><tr><th>Team</th>"
        "<th class='num'>Home W-D-L</th><th class='num'>Goals</th><th class='num'>Pts</th>"
        "<th class='num'>Away W-D-L</th><th class='num'>Goals</th><th class='num'>Pts</th>"
        "<th class='num'>H−A</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
        "<p class='meta'>H−A is home points minus away points: high values are "
        "fortress teams, negative values travel better than they defend home turf.</p>"
    )


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

def xg_table(db):
    rows = db.execute(
        """SELECT team, COUNT(*), SUM(pts), SUM(xpts), SUM(scored), SUM(missed),
                  SUM(xg), SUM(xga), SUM(npxgd)
           FROM understat_team_matches GROUP BY team ORDER BY SUM(pts) DESC, SUM(xpts) DESC"""
    ).fetchall()
    if not rows:
        return ""
    body = ""
    for rank, (team, games, pts, xpts, gf, ga, xg, xga, npxgd) in enumerate(rows, 1):
        luck = pts - xpts
        body += (
            f"<tr><td class='num'>{rank}</td><td>{escape(team)}</td>"
            f"<td class='num'>{games}</td><td class='num score'>{pts}</td>"
            f"<td class='num'>{xpts:.1f}</td><td class='num'>{fmt_delta(luck)}</td>"
            f"<td class='num'>{gf}–{ga}</td><td class='num'>{xg:.1f}</td>"
            f"<td class='num'>{xga:.1f}</td><td class='num'>{fmt_delta(npxgd)}</td></tr>"
        )
    return (
        "<h3>xG table — results vs expected</h3>"
        "<div class='card'><table><thead><tr>"
        "<th class='num'>#</th><th>Team</th><th class='num'>P</th>"
        "<th class='num'>Pts</th><th class='num'>xPts</th><th class='num'>Pts−xPts</th>"
        "<th class='num'>Goals</th><th class='num'>xG</th><th class='num'>xGA</th>"
        "<th class='num'>npxGD</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
        "<p class='meta'>Pts−xPts &gt; 0 means the team has taken more points than its "
        "chances deserved (running hot); npxGD is non-penalty xG difference, the best "
        "single measure of underlying strength.</p>"
    )


def nice_ticks(lo, hi, count=5):
    span = hi - lo
    step = span / (count - 1)
    return [round(lo + i * step, 1) for i in range(count)]


def style_scatter(db):
    rows = db.execute(
        """SELECT team, AVG(ppda), AVG(deep) FROM understat_team_matches
           WHERE ppda IS NOT NULL GROUP BY team"""
    ).fetchall()
    if len(rows) < 2:
        return ""

    width, height = 860, 460
    ml, mr, mt, mb = 55, 130, 15, 45
    plot_w, plot_h = width - ml - mr, height - mt - mb

    xs = [r[1] for r in rows]
    ys = [r[2] for r in rows]
    xpad = (max(xs) - min(xs)) * 0.08 or 1
    ypad = (max(ys) - min(ys)) * 0.08 or 1
    x0, x1 = min(xs) - xpad, max(xs) + xpad
    y0, y1 = min(ys) - ypad, max(ys) + ypad

    def px(v):
        return ml + (v - x0) / (x1 - x0) * plot_w

    def py(v):
        return mt + (1 - (v - y0) / (y1 - y0)) * plot_h

    parts = []
    for tick in nice_ticks(x0, x1):
        x = px(tick)
        parts.append(f"<line class='gridline' x1='{x:.0f}' y1='{mt}' x2='{x:.0f}' y2='{mt + plot_h}'/>")
        parts.append(f"<text x='{x:.0f}' y='{height - 24}' text-anchor='middle'>{tick}</text>")
    for tick in nice_ticks(y0, y1):
        y = py(tick)
        parts.append(f"<line class='gridline' x1='{ml}' y1='{y:.0f}' x2='{ml + plot_w}' y2='{y:.0f}'/>")
        parts.append(f"<text x='{ml - 8}' y='{y:.0f}' text-anchor='end' dominant-baseline='middle'>{tick:.0f}</text>")
    parts.append(f"<text x='{ml + plot_w / 2:.0f}' y='{height - 6}' text-anchor='middle'>"
                 "PPDA — passes allowed per defensive action (left = presses harder)</text>")
    parts.append(f"<text x='14' y='{mt + plot_h / 2:.0f}' text-anchor='middle' "
                 f"transform='rotate(-90 14 {mt + plot_h / 2:.0f})'>Deep completions per match</text>")

    # naive label collision avoidance: nudge labels that land too close
    placed = []
    for team, ppda, deep in sorted(rows, key=lambda r: (py(r[2]), px(r[1]))):
        x, y = px(ppda), py(deep)
        label_y = y + 4
        while any(abs(label_y - oy) < 13 and abs(x - ox) < 105 for ox, oy in placed):
            label_y += 13
        placed.append((x, label_y))
        parts.append(
            f"<circle class='dot' cx='{x:.0f}' cy='{y:.0f}' r='5'>"
            f"<title>{escape(team)}: PPDA {ppda:.1f}, deep completions {deep:.1f} per match</title></circle>"
        )
        parts.append(f"<text class='pt-label' x='{x + 9:.0f}' y='{label_y:.0f}'>{escape(team)}</text>")

    return (
        "<h3>Team style — pressing vs territory</h3>"
        "<div class='chart-card'>"
        f"<svg viewBox='0 0 {width} {height}' width='100%' role='img' "
        "aria-label='Scatter plot of pressing intensity against deep completions per team'>"
        + "".join(parts) + "</svg></div>"
        "<p class='meta'>Season averages. Left = allows few opposition passes per "
        "defensive action (aggressive press); top = completes many passes near the "
        "opponent box (territorial dominance). Top-left teams press high and pin "
        "opponents back; bottom-right teams sit deep and go direct. Hover a dot for "
        "exact values.</p>"
    )


def rolling_sparklines(db):
    rows = db.execute(
        """SELECT team, npxgd FROM understat_team_matches
           ORDER BY team, match_date"""
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
        "SELECT team FROM understat_team_matches GROUP BY team ORDER BY SUM(pts) DESC"
    ) if r[0] in rolling]

    w, h = 158, 46
    cells = []
    for team in order:
        values = rolling[team]
        step = w / (len(values) - 1)
        points = " ".join(
            f"{i * step:.1f},{h / 2 - (v / max_abs) * (h / 2 - 3):.1f}"
            for i, v in enumerate(values)
        )
        last = values[-1]
        cells.append(
            f"<div class='spark'><p class='name'>{escape(team)}"
            f"<span class='val'>{fmt_delta(last, 2)}</span></p>"
            f"<svg viewBox='0 0 {w} {h}' width='100%' role='img' "
            f"aria-label='{escape(team)} rolling xG difference'>"
            f"<title>{escape(team)}: rolling {ROLLING_WINDOW}-match npxGD, "
            f"season range {fmt_delta(min(values), 2)} to {fmt_delta(max(values), 2)}, "
            f"latest {fmt_delta(last, 2)}</title>"
            f"<line class='zeroline' x1='0' y1='{h / 2}' x2='{w}' y2='{h / 2}'/>"
            f"<polyline class='curve' points='{points}'/></svg></div>"
        )
    return (
        "<h3>Form curves — rolling xG difference</h3>"
        f"<div class='chart-card'><div class='spark-grid'>{''.join(cells)}</div></div>"
        f"<p class='meta'>Non-penalty xG difference averaged over the last {ROLLING_WINDOW} "
        f"matches, across the season (teams in final-table order; all curves share the same "
        f"scale, ±{max_abs:.1f}). Above the dashed line = creating more than conceding. The "
        "number is the value at season's end. Hover a curve for its range.</p>"
    )


# -------------------------------------------------------------- player tab

def finishing_rows(db, order, limit=8, min_minutes=900):
    return db.execute(
        f"""SELECT player_name, team, minutes, shots, goals, xg, goals - xg AS diff
            FROM understat_players WHERE minutes >= ?
            ORDER BY diff {order} LIMIT ?""",
        (min_minutes, limit),
    ).fetchall()


def player_table(rows, value_header):
    body = ""
    for name, team, minutes, shots, goals, xg, diff in rows:
        body += (
            f"<tr><td>{escape(name)}</td><td class='dim'>{escape(team)}</td>"
            f"<td class='num'>{minutes}</td><td class='num'>{shots}</td>"
            f"<td class='num'>{goals}</td><td class='num'>{xg:.1f}</td>"
            f"<td class='num score'>{fmt_delta(diff)}</td></tr>"
        )
    return (
        "<div class='card'><table><thead><tr>"
        "<th>Player</th><th>Team</th><th class='num'>Min</th><th class='num'>Shots</th>"
        f"<th class='num'>Goals</th><th class='num'>xG</th><th class='num'>{value_header}</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )


def creators_table(db, limit=8, min_minutes=900):
    rows = db.execute(
        """SELECT player_name, team, minutes, key_passes, assists, xa, assists - xa
           FROM understat_players WHERE minutes >= ?
           ORDER BY xa DESC LIMIT ?""",
        (min_minutes, limit),
    ).fetchall()
    body = ""
    for name, team, minutes, key_passes, assists, xa, diff in rows:
        body += (
            f"<tr><td>{escape(name)}</td><td class='dim'>{escape(team)}</td>"
            f"<td class='num'>{minutes}</td><td class='num'>{key_passes}</td>"
            f"<td class='num'>{assists}</td><td class='num'>{xa:.1f}</td>"
            f"<td class='num score'>{fmt_delta(diff)}</td></tr>"
        )
    return (
        "<div class='card'><table><thead><tr>"
        "<th>Player</th><th>Team</th><th class='num'>Min</th><th class='num'>Key passes</th>"
        "<th class='num'>Assists</th><th class='num'>xA</th><th class='num'>A−xA</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )


def player_explorer(db):
    rows = db.execute(
        """SELECT player_name, team, position, games, minutes, goals, xg,
                  assists, xa, shots, key_passes, npg, npxg
           FROM understat_players ORDER BY xg DESC"""
    ).fetchall()
    if not rows:
        return ""
    players = [
        {
            "name": r[0], "team": r[1], "pos": r[2] or "", "games": r[3],
            "min": r[4], "goals": r[5], "xg": round(r[6], 2),
            "assists": r[7], "xa": round(r[8], 2), "shots": r[9], "kp": r[10],
            "npg": r[11], "npxg": round(r[12], 2),
            "gdiff": round(r[5] - r[6], 2), "adiff": round(r[7] - r[8], 2),
        }
        for r in rows
    ]
    # transferred players have comma-joined teams ("Inter,Parma"); offer single clubs
    teams = sorted({club for p in players for club in p["team"].split(",")})
    team_options = "".join(f"<option>{escape(t)}</option>" for t in teams)
    payload = json.dumps(players, ensure_ascii=False).replace("</", "<\\/")

    return (
        "<h3>Player explorer</h3>"
        "<div class='controls'>"
        "<input type='search' id='pe-search' placeholder='Search player or team…'>"
        f"<select id='pe-team'><option value=''>All teams</option>{team_options}</select>"
        "<select id='pe-pos'><option value=''>All positions</option>"
        "<option value='G'>Goalkeepers</option><option value='D'>Defenders</option>"
        "<option value='M'>Midfielders</option><option value='F'>Forwards</option></select>"
        "<label>Min minutes <input type='number' id='pe-min' value='0' min='0' step='90'></label>"
        "<label><input type='checkbox' id='pe-per90'> per 90</label>"
        "<span class='count' id='pe-count'></span>"
        "</div>"
        "<div class='card'><table id='player-table'><thead><tr></tr></thead>"
        "<tbody></tbody></table></div>"
        "<p class='meta'>Every player Understat tracks this season. Click a column "
        "header to sort; “per 90” converts volume stats to per-90-minute rates "
        "(players under 270 minutes are hidden in that mode to avoid tiny-sample noise).</p>"
        f"<script>const PLAYERS = {payload};</script>"
    )


EXPLORER_JS = """
(function () {
  if (typeof PLAYERS === 'undefined') return;
  const COLS = [
    { key: 'name',    label: 'Player' },
    { key: 'team',    label: 'Team' },
    { key: 'pos',     label: 'Pos' },
    { key: 'min',     label: 'Min',   num: true },
    { key: 'games',   label: 'Apps',  num: true },
    { key: 'goals',   label: 'Goals', num: true, per90: true },
    { key: 'xg',      label: 'xG',    num: true, per90: true, dec: 1 },
    { key: 'gdiff',   label: 'G−xG',  num: true, dec: 1, signed: true },
    { key: 'assists', label: 'A',     num: true, per90: true },
    { key: 'xa',      label: 'xA',    num: true, per90: true, dec: 1 },
    { key: 'adiff',   label: 'A−xA',  num: true, dec: 1, signed: true },
    { key: 'shots',   label: 'Shots', num: true, per90: true },
    { key: 'kp',      label: 'KP',    num: true, per90: true },
    { key: 'npxg',    label: 'npxG',  num: true, per90: true, dec: 1 }
  ];
  const state = { sortKey: 'xg', sortDir: -1, per90: false };
  const $ = (id) => document.getElementById(id);
  const thead = document.querySelector('#player-table thead tr');
  const tbody = document.querySelector('#player-table tbody');

  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

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
      return "<th class='sortable" + (col.num ? " num" : "") + "' data-key='" + col.key +
             "'>" + col.label + "<span class='arrow'>" + arrow + "</span></th>";
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
    tbody.innerHTML = rows.map((p) =>
      '<tr>' + COLS.map((c, i) => {
        const cls = c.num ? 'num' : (i === 1 || i === 2 ? 'dim' : '');
        const strong = c.key === state.sortKey ? ' score' : '';
        return "<td class='" + cls + strong + "'>" + display(p, c) + '</td>';
      }).join('') + '</tr>'
    ).join('');
    $('pe-count').textContent = rows.length + ' of ' + PLAYERS.length + ' players';
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
  const initial = location.hash.slice(1);
  activate(document.getElementById('panel-' + initial) ? initial : tabs[0].dataset.panel);
})();
"""


# ------------------------------------------------------------------- report

def league_section(db, league):
    return (
        f"<h2>{escape(league)}</h2>"
        "<h3>Standings</h3>" + standings_table(db, league)
        + home_away_table(db, league)
        + "<h3>Recent results</h3>" + matches_table(db, league, finished=True)
        + "<h3>Upcoming fixtures</h3>" + matches_table(db, league, finished=False)
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


def teams_panel(db):
    return (
        f"<h2>Team analytics <span class='dim'>({season_label(db)}, Understat)</span></h2>"
        + xg_table(db) + style_scatter(db) + rolling_sparklines(db)
    )


def players_panel(db):
    return (
        f"<h2>Players <span class='dim'>({season_label(db)}, Understat)</span></h2>"
        + player_explorer(db)
        + "<h3>Clinical finishers — most goals above xG</h3>"
        + player_table(finishing_rows(db, "DESC"), "G−xG")
        + "<h3>Wasteful in front of goal — most goals below xG</h3>"
        + player_table(finishing_rows(db, "ASC"), "G−xG")
        + "<h3>Top creators by expected assists</h3>"
        + creators_table(db)
        + "<p class='meta'>Boards show players with at least 900 minutes. xG = expected "
        "goals from chance quality; a striker far above xG is finishing exceptionally "
        "(or running hot), far below is missing good chances. xA is the same for passes.</p>"
    )


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit("No football.sqlite found - run `python fetch_data.py` first.")
    db = sqlite3.connect(DB_PATH)
    leagues = [
        r[0] for r in db.execute("SELECT DISTINCT league FROM matches ORDER BY league")
        if r[0] not in HIDDEN_LEAGUES
    ]
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    panels = [("league", "League", "".join(league_section(db, lg) for lg in leagues))]
    if understat_available(db):
        panels.append(("teams", "Team analytics", teams_panel(db)))
        panels.append(("players", "Players", players_panel(db)))

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

    html = (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Football report</title><style>{CSS}</style></head><body><div class='wrap'>"
        f"<h1>Football report</h1>"
        f"<p class='meta'>Generated {generated} · data from TheSportsDB and Understat</p>"
        + tab_bar + panel_html
        + "<footer>Standings are computed from the stored results. Run "
        "<code>python fetch_data.py</code> and <code>python fetch_understat.py</code> "
        "regularly to keep the database current.</footer></div>"
        f"<script>{EXPLORER_JS}</script></body></html>"
    )
    REPORT_PATH.write_text(html, encoding="utf-8")
    db.close()
    print(f"Report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
