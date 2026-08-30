"""Build a self-contained HTML report from the local football.sqlite database.

Run `python fetch_data.py` (and `python fetch_understat.py`) first, then:

    python build_report.py

The result is report.html next to this script — open it in any browser.
Uses only the Python standard library; the page itself uses a little vanilla
JavaScript for tabs and the player explorer (works offline from file://).
"""

import json
import math
import random
import re
import sqlite3
import subprocess
import sys
import unicodedata

from bisect import bisect
from datetime import date, datetime, timedelta, timezone
from html import escape, unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import prediction_log
import projection_log

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "football.sqlite"
REPORT_PATH = PROJECT_DIR / "report.html"
DOCS_PATH = PROJECT_DIR / "docs" / "index.html"  # committed copy, served by GitHub Pages

# Leagues kept in the database but left out of the report for now
HIDDEN_LEAGUES = set()  # Allsvenskan came off the bench 2026-07-17 (FotMob xG)

# Preferred order of the league switcher; anything else stored comes after
LEAGUE_ORDER = ["Serie A", "Premier League", "La Liga", "Bundesliga", "Ligue 1",
                "Allsvenskan"]

# leagues whose advanced stats come from Understat; Allsvenskan's come from
# FotMob instead (no PPDA / deep completions / xGChain, but real per-match xG)
UNDERSTAT_LEAGUES = ["Serie A", "Premier League", "La Liga", "Bundesliga", "Ligue 1"]

FORM_WINDOW = 5     # matches shown in the form column
TREND_WINDOW = 5    # rounds used for the rank-trend arrow
ROLLING_WINDOW = 5  # matches in the rolling xG-difference curves

CSS = """
:root {
  --surface: #f7f7f4; --card: #ffffff; --border: #e4e3df;
  --text-primary: #101010; --text-secondary: #52514e;
  --accent: #2a78d6; --accent-2: #7c5cff;
  --win: #0ca30c; --loss: #d03b3b; --draw: #8a8983;
  --away: #eb6834;  /* predictions bar away pole; validated vs --accent for CVD */
  --row-hover: #f0f4fa; --row-alt: rgba(16,16,16,.026);
  --glow: rgba(42,120,214,.10); --glow-2: rgba(124,92,255,.08);
  --shadow: 0 1px 2px rgba(20,20,20,.05), 0 4px 16px rgba(20,20,20,.05);
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #161615; --card: #212120; --border: #3a3936;
    --text-primary: #ffffff; --text-secondary: #c3c2b7;
    --accent: #3987e5; --accent-2: #9d86ff; --draw: #75746e;
    --away: #e0602e;
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
h4 { font-size: 13px; margin: 18px 0 7px; color: var(--text-secondary);
     font-weight: 600; }
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
nav.seasonnav { margin: 18px 0 0; }
nav.seasonnav label { font-size: 13px; color: var(--text-secondary); margin-right: 6px; }
nav.seasonnav select {
  font: inherit; font-size: 13px; font-weight: 600; color: var(--text-primary);
  background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  padding: 6px 10px; cursor: pointer;
}
nav.seasonnav select:hover { border-color: var(--accent); }
.subnav { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0 2px; }
.subnav a {
  font-size: 12px; font-weight: 600; color: var(--text-secondary);
  border: 1px solid var(--border); background: var(--card); border-radius: 999px;
  padding: 4px 12px; cursor: pointer; user-select: none;
}
.subnav a:hover { color: var(--accent); border-color: var(--accent); }
.gs-bar { margin: 14px 0 0; }
#gs-open {
  display: inline-flex; align-items: center; gap: 9px; width: min(400px, 100%);
  font: inherit; font-size: 13px; color: var(--text-secondary); text-align: left;
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 8px 12px; cursor: pointer;
}
#gs-open:hover { border-color: var(--accent); color: var(--text-primary); }
#gs-open .gs-ph { flex: 1; }
#gs-open kbd {
  font: inherit; font-size: 11px; font-weight: 600; color: var(--text-secondary);
  background: var(--surface); border: 1px solid var(--border);
  border-bottom-width: 2px; border-radius: 5px; padding: 0 5px;
}
#gs-fab {
  position: fixed; right: 22px; bottom: 72px; z-index: 20;
  width: 42px; height: 42px; border-radius: 50%;
  border: 1px solid var(--border); background: var(--card); color: var(--text-secondary);
  cursor: pointer; box-shadow: var(--shadow);
  opacity: 0; pointer-events: none; transition: opacity .2s;
}
#gs-fab.show { opacity: 1; pointer-events: auto; }
#gs-fab:hover { color: var(--accent); border-color: var(--accent); }
#gs-overlay {
  position: fixed; inset: 0; background: rgba(10,10,10,.55); z-index: 40;
  display: flex; align-items: flex-start; justify-content: center;
  padding: 9vh 16px 16px;
}
#gs-overlay[hidden] { display: none; }
#gs-modal {
  background: var(--card); border: 1px solid var(--border); border-radius: 12px;
  box-shadow: var(--shadow); width: 100%; max-width: 560px; overflow: hidden;
}
#gs-input {
  width: 100%; font: inherit; font-size: 16px; color: var(--text-primary);
  background: none; border: none; border-bottom: 1px solid var(--border);
  padding: 14px 16px; outline: none;
}
#gs-results { max-height: min(52vh, 430px); overflow-y: auto; }
.gs-group {
  font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--text-secondary); padding: 11px 16px 4px;
}
.gs-item {
  display: flex; align-items: baseline; gap: 12px; padding: 7px 16px; cursor: pointer;
}
.gs-item[aria-selected="true"] { background: var(--row-hover); }
.gs-name { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.gs-name mark { background: none; color: var(--accent); }
.gs-sub {
  margin-left: auto; font-size: 12px; color: var(--text-secondary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.gs-empty { margin: 0; padding: 16px; font-size: 13px; color: var(--text-secondary); }
.gs-foot {
  margin: 0; padding: 8px 16px; font-size: 11.5px; color: var(--text-secondary);
  border-top: 1px solid var(--border);
}
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
.caveat { border: 1px solid rgba(217, 119, 6, .4); border-left: 4px solid #d97706;
          background: rgba(217, 119, 6, .08); border-radius: 12px;
          padding: 12px 16px; margin: 16px 0 6px; }
.caveat p { margin: 4px 0; }
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
.range-grid { display: flex; flex-direction: column; gap: 3px; }
.range-row { display: flex; align-items: center; gap: 10px; }
.range-name {
  flex: 0 0 150px; font-size: 12.5px; color: var(--text-primary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.range-name b { color: var(--text-secondary); font-weight: 600; margin-right: 4px; }
.range-track { position: relative; flex: 1; height: 22px; }
.range-track i.rz { position: absolute; top: 0; bottom: 0; border-radius: 2px; }
.range-track i.rz.win { background: color-mix(in srgb, var(--accent-2) 14%, transparent); }
.range-track i.rz.cl { background: color-mix(in srgb, var(--accent) 10%, transparent); }
.range-track i.rz.rel { background: color-mix(in srgb, var(--loss) 10%, transparent); }
.range-whisker {
  position: absolute; top: 50%; height: 2px; border-radius: 1px;
  background: var(--text-secondary); opacity: .55; transform: translateY(-50%);
}
.range-box {
  position: absolute; top: 4px; bottom: 4px; border-radius: 3px;
  background: color-mix(in srgb, var(--accent) 55%, transparent);
}
.range-median {
  position: absolute; top: 1px; bottom: 1px; width: 2px;
  background: var(--text-primary); transform: translateX(-1px);
}
.sim-card .controls { margin: 4px 4px 14px; }
.sim-fixtures { list-style: none; margin: 0; padding: 0; columns: 2; column-gap: 24px; }
@media (max-width: 640px) { .sim-fixtures { columns: 1; } }
.sim-fixtures li { font-size: 13px; padding: 3px 0; white-space: nowrap; }
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
.h2h-h { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;
         color: var(--text-secondary); margin: 20px 0 8px; font-weight: 700; }
/* recent form under a single club's profile; the table itself reuses .fx-form */
.tc-form { margin-top: 4px; }
.tc-chips { display: flex; align-items: center; flex-wrap: wrap; gap: 4px;
            margin: 0 2px 8px; font-size: 12.5px; }
.tc-chips .dim { margin-left: 6px; }
.h2h-metric { margin: 9px 0; }
.h2h-lab { text-align: center; font-size: 11px; color: var(--text-secondary);
           text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 2px; }
.h2h-row { display: flex; align-items: center; gap: 10px; }
.h2h-val { flex: 0 0 108px; font-size: 13px; font-variant-numeric: tabular-nums; }
.h2h-val.a { text-align: right; }
.h2h-val.lead { font-weight: 700; }
.h2h-bar { flex: 1; display: flex; height: 10px; border-radius: 5px; overflow: hidden;
           background: var(--surface); border: 1px solid var(--border); }
.h2h-bar i { display: block; height: 100%; }
.h2h-bar i.a { background: var(--accent); }
.prob { display: flex; gap: 2px; height: 18px; min-width: 170px; }
.prob i {
  font-style: normal; height: 100%; border-radius: 3px; overflow: hidden;
  font-size: 11px; font-weight: 600; line-height: 18px; color: #fff;
  text-align: center; white-space: nowrap;
}
.prob i.h { background: var(--accent); }
.prob i.d { background: var(--draw); }
.prob i.a { background: var(--away); }
.changelog { margin: 12px 0 0; max-width: 780px; }
.changelog > summary { font-size: 13.5px; }
.cl-date { margin: 10px 0 4px; font-size: 12px; text-transform: uppercase;
  letter-spacing: .05em; color: var(--text-secondary); }
.cl-date:first-child { margin-top: 2px; }
.cl-list { margin: 0; padding-left: 18px; }
.cl-list li { margin: 3px 0; font-size: 13.5px; color: var(--text-secondary); }
.cl-list strong { color: var(--text-primary); }
/* a club link says so at rest, not only under the cursor: the trouble with
   these tables was never that the link failed, it was not being able to tell
   which part of a row led where before clicking it */
.team-link { cursor: pointer; text-decoration: underline dotted;
  text-decoration-color: var(--border); text-underline-offset: 3px; }
.team-link:hover { color: var(--accent); text-decoration: underline solid;
  text-decoration-color: currentColor; text-underline-offset: 2px; }
/* the same marking, inert: an example of a club link inside a hint */
.link-eg { text-decoration: underline dotted;
  text-decoration-color: var(--border); text-underline-offset: 3px; }
.tc-squad { margin-top: 12px; }
/* a squad name leads to the player card exactly the way a club name leads to
   the club card, so it is marked the same way */
.squad-link { cursor: pointer; text-decoration: underline dotted;
  text-decoration-color: var(--border); text-underline-offset: 3px; }
.squad-link:hover { color: var(--accent); text-decoration: underline solid;
  text-decoration-color: currentColor; text-underline-offset: 2px; }
.team-link:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px;
  border-radius: 3px; }
tr.fx-link { cursor: pointer; }
tr.fx-link:hover td { background: var(--row-hover); }
tr.fx-link:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
/* a quiet chevron that only shows the row is live on hover/focus */
tr.fx-link td:last-child { position: relative; }
tr.fx-link td:last-child::after {
  content: '\\203A'; position: absolute; right: 6px; top: 50%;
  transform: translateY(-50%); color: var(--accent); opacity: 0;
  font-weight: 700; transition: opacity .12s;
}
tr.fx-link:hover td:last-child::after,
tr.fx-link:focus-visible td:last-child::after { opacity: 1; }
.fx-head { display: flex; justify-content: space-between; align-items: baseline;
  gap: 12px; flex-wrap: wrap; margin: 2px 2px 12px; }
.fx-head h4 { margin: 0; font-size: 19px; }
.fx-score { font-variant-numeric: tabular-nums; padding: 0 4px; }
.fx-xg { display: flex; align-items: baseline; gap: 10px; margin: 0 2px 4px;
  font-size: 13.5px; color: var(--text-secondary); }
.fx-xg b { font-size: 17px; color: var(--text-primary);
  font-variant-numeric: tabular-nums; }
.fx-grade { display: inline-block; padding: 1px 7px; border-radius: 999px;
  font-size: 11.5px; font-weight: 700; margin-left: 4px; color: #fff; }
.fx-grade.ok { background: var(--win); }
.fx-grade.no { background: var(--loss); }
.fx-verdict { margin: 0 2px 6px; }
.fx-verdict .prob { height: 22px; }
.fx-luck { border-left: 3px solid var(--accent); background: var(--surface);
  border-radius: 0 6px 6px 0; padding: 8px 12px; margin: 8px 2px 0;
  font-size: 13.5px; }
.fx-noverdict { border: 1px solid var(--border); border-left: 3px solid var(--draw);
  background: var(--surface); border-radius: 6px; padding: 9px 12px; margin: 0 2px 6px;
  font-size: 13.5px; color: var(--text-secondary); }
.fx-names { font-weight: 600; margin: 16px 0 0; }
.fx-h { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--text-secondary); margin: 18px 2px 6px; font-weight: 600; }
.fx-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 28px; }
.fx-none { font-size: 13px; margin: 4px 2px; }
.fx-form { width: 100%; border-collapse: collapse; font-size: 13px; }
.fx-form td, .fx-form th { padding: 3px 6px; border-bottom: 1px solid var(--border);
  white-space: nowrap; }
.fx-form th { font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
  color: var(--text-secondary); text-align: left; }
.fx-form tr:last-child td { border-bottom: 0; }
.tc-hist { margin-top: 10px; }
.hist-gap td { opacity: .5; font-style: italic; }
.hist-now td { background: rgba(127, 127, 127, .10); }
.hist-bar { position: relative; display: inline-block; width: 96px; height: 10px;
            vertical-align: middle; }
.hist-bar::before { content: ''; position: absolute; left: 50%; top: 0; bottom: 0;
                    width: 1px; background: var(--border); }
.hist-bar i { position: absolute; top: 2px; bottom: 2px; border-radius: 2px; }
.hist-bar i.over { background: var(--win); }
.hist-bar i.under { background: var(--loss); }
.hist-n { display: inline-block; min-width: 46px; text-align: right; }
.hist-n.over { color: var(--win); }
.hist-n.under { color: var(--loss); }
.pd-career { margin-top: 14px; }
.pd-career table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.pd-career td, .pd-career th { padding: 3px 6px; white-space: nowrap;
                               border-bottom: 1px solid var(--border); }
.pd-career th { font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
                text-align: left; color: var(--muted); }
.pd-career tr:last-child td { border-bottom: 0; }
.pd-career .car-grow { width: 100%; white-space: normal; }
.pd-career .car-gap td { opacity: .5; font-style: italic; }
.pd-career .car-away { opacity: .78; }
/* the one column carrying a name absorbs the slack; everything else is a
   chip, a date or a number and stays as narrow as its content */
.fx-form td, .fx-form th { width: 1%; }
.fx-form .fx-grow { width: 100%; }
.fx-season { margin: 0 2px 4px; font-size: 12px; }
/* head-to-head is one full-width table, so pin the middle columns together
   instead of letting the club names drift to opposite edges. No result chip
   here on purpose — see h2hBlock; the winner is marked on the name instead */
.fx-h2h { max-width: 620px; }
.fx-h2h td:nth-child(2) { text-align: right; }
.fx-won { font-weight: 700; color: var(--text-primary); }
/* the all-matches strip sits under the venue one as the quieter reference */
.pd-totals.fx-all { margin-top: 6px; opacity: .78; }
@media (max-width: 720px) { .fx-cols { grid-template-columns: 1fr; } }
.pdot {
  display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  vertical-align: -1px; margin-right: 4px;
}
/* season projection: the percentage sits on top of its own bar, so a column
   of them reads as a shape before it reads as numbers */
td.pcell {
  position: relative; text-align: right;
  /* the number is nudged clear of the cell edge so the bar always has room
     to show a sliver behind it, even at 1% */
  padding-right: 14px;
}
td.pcell i {
  /* Anchored right, growing out from under its own number. Kept faint on
     purpose: at full strength the bar's edge slicing between two digits of
     a number like 31% is the first thing the eye lands on. */
  position: absolute; right: 4px; top: 5px; bottom: 5px; border-radius: 2px;
  min-width: 3px;
}
td.pcell i.win { background: color-mix(in srgb, var(--accent-2) 22%, transparent); }
td.pcell i.cl { background: color-mix(in srgb, var(--accent) 20%, transparent); }
td.pcell i.rel { background: color-mix(in srgb, var(--loss) 20%, transparent); }
td.pcell span { position: relative; }
.h2h-bar i.b { background: var(--win); margin-left: auto; }
.h2h-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 28px; }
@media (max-width: 760px) { .h2h-cols { grid-template-columns: 1fr; } }
svg .h2h-line { fill: none; stroke-width: 2; }
svg .h2h-line.a { stroke: var(--accent); }
svg .h2h-line.b { stroke: var(--win); }
svg .h2h-dot { stroke: var(--card); stroke-width: 1; }
svg .h2h-dot.a { fill: var(--accent); }
svg .h2h-dot.b { fill: var(--win); }
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


# ------------------------------------------------------- cross-tab linking

def _team_link_map(db, league):
    """League-tab club name -> the name Team analytics knows it by.

    Only clubs that tab actually covers get an entry. That is a live
    constraint, not a theoretical one: Team analytics is built from this
    season's matches, so two days into a campaign it holds 8 of Serie A's 20
    clubs, and a link for the other twelve would go nowhere.

    The two feeds also disagree about names ("Inter Milan" against Understat's
    "Inter"), which _predict_mapping already knows how to bridge.
    """
    names = [t["team"] for t in load_teams(db, league)]
    if not names:
        return {}
    display = sorted({r[0] for r in db.execute(
        "SELECT DISTINCT home_team FROM matches WHERE league = ?", (league,))
        if r[0]})
    if not display:
        return {}
    return {d: a for d, a in _predict_mapping(display, names).items() if a}


def _analytics_label(name, league=None):
    """A club's name as a link, for the tables built from the xG feed itself.

    No name map, unlike the League tab: those tables come from TheSportsDB and
    have to be bridged to the club card's names, while these are already
    reading the rows the card is built from. A club the card somehow does not
    hold simply stays unmarked, because the marking is applied client-side
    against the list the card actually has.

    league is only needed where a table mixes them -- Best of Europe ranks all
    five at once, and a row there has to say which one it belongs to or the
    click resolves against whichever league the page happens to be showing.
    """
    label = escape(name or "")
    lg = f" data-lg='{escape(league)}'" if league else ""
    return f"<span data-team='{label}'{lg}>{label}</span>"


def _team_label(name, tmap):
    """A club's name, wrapped in the link when Team analytics covers it.

    The name, not the cell it sits in. In a results row the whole row already
    opens the match, so a cell-wide club link puts two targets in one
    rectangle with nothing to mark the border: click an inch to the right of
    "Arsenal" and you get the match instead of the club, having had no way to
    know. Wrapping the text makes the hit area exactly the part that
    underlines, and leaves the rest of the cell to the row.
    """
    target = tmap.get(name)
    label = escape(name or "")
    return f"<span data-team='{escape(target)}'>{label}</span>" if target else label


# ---------------------------------------------------------------- standings

def completed_matches(db, league):
    return db.execute(
        """SELECT round, match_date, home_team, away_team, home_score, away_score
           FROM matches WHERE league = ? AND home_score IS NOT NULL
           ORDER BY match_date, event_id""",
        (league,),
    ).fetchall()


def pretty_date(iso):
    """2026-08-21 -> 21 August 2026; passes anything unparseable straight back."""
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return iso or ""
    return f"{d.day} {d.strftime('%B')} {d.year}"


def league_season_state(db, league):
    """Which season each half of the League tab is actually describing.

    Out of season these are two different things. The table, the results and
    the home/away splits come from the last season that has completed
    matches; the fixtures, predictions and projection have already moved on
    to the next one. That is the right behaviour — a finished table is still
    the most useful thing to show in August — but presented without a word of
    explanation it reads as though the league is live and the model is
    predicting matches that contradict the table above it.

    Returns (table_season, next_season, next_start) where next_season is None
    unless the table's season is genuinely over and another one is scheduled.
    Once the new season plays its first match it becomes the anchor, and this
    collapses back to (season, None, None) on its own.
    """
    table_season = db.execute(
        "SELECT MAX(season) FROM matches WHERE league = ? AND home_score IS NOT NULL",
        (league,),
    ).fetchone()[0]
    if not table_season:
        return None, None, None
    still_to_play = db.execute(
        "SELECT COUNT(*) FROM matches WHERE league = ? AND season = ? "
        "AND home_score IS NULL",
        (league, table_season),
    ).fetchone()[0]
    if still_to_play:
        return table_season, None, None   # season in progress, nothing to explain
    nxt = db.execute(
        """SELECT season, MIN(match_date) FROM matches
           WHERE league = ? AND home_score IS NULL AND season > ?
           GROUP BY season ORDER BY season LIMIT 1""",
        (league, table_season),
    ).fetchone()
    if not nxt or not nxt[0]:
        return table_season, None, None
    return table_season, nxt[0], nxt[1]


def between_seasons_note(table_season, next_season, next_start):
    """Says out loud that the tab is straddling two seasons."""
    if not next_season:
        return ""
    when = pretty_date(next_start)
    starts = f" It starts on {escape(when)}." if when else ""
    return (
        "<div class='caveat'><strong>Between seasons.</strong> "
        f"{escape(str(table_season))} is over, so the table, results and "
        "home/away splits below are how that season <em>finished</em> — not a "
        "live table. The fixtures, predictions and season projection are "
        f"already for {escape(str(next_season))}.{starts} Everything realigns "
        "by itself once the new season plays its first match.</div>"
    )


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


def standings_table(db, league, title_suffix="", trend=True):
    matches = completed_matches(db, league)
    if not matches:
        return "<p class='dim'>No completed matches in the database yet.</p>"
    table = compute_table(matches)

    max_round = max((m[0] for m in matches if m[0] is not None), default=None)
    previous_rank = {}
    # a frozen season turns the trend column off rather than filling it with
    # dashes: "climbing or sliding" is a question about a table still moving
    if trend and max_round and max_round > TREND_WINDOW:
        earlier = compute_table(matches, upto_round=max_round - TREND_WINDOW)
        previous_rank = {team: i for i, (team, _) in enumerate(earlier, 1)}

    tmap = _team_link_map(db, league)
    body = ""
    for rank, (team, t) in enumerate(table, 1):
        change = previous_rank[team] - rank if team in previous_rank else None
        zone = " class='zone-cl'" if rank <= 4 else " class='zone-rel'" if rank > len(table) - 3 else ""
        body += (
            f"<tr{zone}><td class='num'>{rank}</td>"
            f"<td>{_team_label(team, tmap)}</td>"
            f"<td class='num'>{t['p']}</td><td class='num'>{t['w']}</td>"
            f"<td class='num'>{t['d']}</td><td class='num'>{t['l']}</td>"
            f"<td class='num'>{t['gf']}–{t['ga']}</td>"
            f"<td class='num'>{t['gf'] - t['ga']:+d}</td>"
            f"<td class='num score'>{t['pts']}</td>"
            + (f"<td class='num'>{trend_arrow(change)}</td>" if trend else "")
            + f"<td>{form_chips(team_form(db, league, team))}</td></tr>"
        )
    card = (
        "<div class='card'><table><thead><tr>"
        "<th class='num'>#</th><th>Team</th><th class='num'>P</th>"
        "<th class='num'>W</th><th class='num'>D</th><th class='num'>L</th>"
        "<th class='num'>Goals</th><th class='num'>+/−</th><th class='num'>Pts</th>"
        + (f"<th class='num'>±{TREND_WINDOW}R</th>" if trend else "")
        + f"<th>Form</th></tr></thead><tbody>{body}</tbody></table></div>"
    )
    about = (
        "<p><strong>What it shows.</strong> The league table, computed from every stored "
        "result rather than copied from a website — wins are 3 points, draws 1; ties are "
        "broken by goal difference, then goals scored.</p>"
        "<p><strong>The extras.</strong> "
        + (f"±{TREND_WINDOW}R is each team's change in league position over the "
           f"last {TREND_WINDOW} rounds — a quick read on who is climbing or "
           "sliding. " if trend else "")
        + f"The form chips are the last {FORM_WINDOW} results, oldest to newest. "
        "A blue stripe marks the top four (Champions League places), a red stripe the "
        "bottom three (relegation).</p>"
    )
    hint = ("<p class='meta team-hint' hidden>Click a club for its style "
            "profile in Team analytics.</p>")
    return block("Standings" + title_suffix, hint + card, about)


def home_away_table(db, league, title_suffix=""):
    matches = completed_matches(db, league)
    if not matches:
        return ""
    body = ""
    tmap = _team_link_map(db, league)
    for team, t in compute_table(matches):
        h, a = t["home"], t["away"]
        body += (
            f"<tr><td>{_team_label(team, tmap)}</td>"
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
    return block("Home / away split" + title_suffix, card, about)


# ------------------------------------------------------------ matches lists

def matches_table(db, league, finished, limit=10):
    if finished:
        rows = db.execute(
            """SELECT match_date, round, home_team, home_score, away_score, away_team,
                      event_id
               FROM matches WHERE league = ? AND home_score IS NOT NULL
               ORDER BY match_date DESC, event_id LIMIT ?""",
            (league, limit),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT match_date, round, home_team, home_score, away_score, away_team,
                      event_id
               FROM matches WHERE league = ? AND home_score IS NULL AND match_date >= ?
               ORDER BY match_date, event_id LIMIT ?""",
            (league, date.today().isoformat(), limit),
        ).fetchall()
    if not rows:
        return "<p class='dim'>No matches in the database yet.</p>"
    tmap = _team_link_map(db, league)
    body = ""
    for match_date, rnd, home, hs, as_, away, event_id in rows:
        score = f"<span class='score'>{hs} – {as_}</span>" if hs is not None else "<span class='dim'>vs</span>"
        rnd_label = f"R{rnd}" if rnd else ""
        # every row carries its id so the explorer can open it — an upcoming
        # fixture as a preview, a played one as a match report. The explorer
        # marks up only the ids it actually holds, so a match beyond either
        # slate simply stays inert
        fx = f" data-fx='{escape(str(event_id))}'"
        body += (
            f"<tr{fx}><td class='dim'>{escape(match_date or '')}</td><td class='dim'>{rnd_label}</td>"
            f"<td style='text-align:right'>{_team_label(home, tmap)}</td>"
            f"<td style='text-align:center'>{score}</td>"
            f"<td>{_team_label(away, tmap)}</td></tr>"
        )
    return f"<div class='card'><table><tbody>{body}</tbody></table></div>"


# ------------------------------------------------------------- predictions

PREDICT_HALF_LIFE_DAYS = 180  # a match this old carries half the weight
PREDICT_LOOKBACK_DAYS = 1400  # nearly four seasons. A long window with a
                              # SHORT half-life beat the old 400/240 pair on
                              # held-out data (model_lab.py memory sweep):
                              # a hard cutoff was doing the job of decay
                              # badly, throwing away faint but real evidence
                              # of how good a club is. The surface is flat
                              # from ~730 days on, so this is not a knife edge
PREDICT_GOALS_BLEND = 0.3     # strengths = 70% non-penalty xG + 30% actual
                              # goals; the backtest's best variant, and the
                              # weight sweep's optimum (0.1-0.5 tried)
PREDICT_DEEP_POWER = 0.15     # territory term: attack scaled by (team deep
                              # completions / league average) ** this. Chosen
                              # on pre-2021 seasons, gain held on 2021+; PPDA
                              # tested the same way came out at exactly zero.
                              # Leagues without the metric (Allsvenskan) skip it.
PREDICT_MAX_GOALS = 10        # Poisson score grid per side
PREDICT_SHOWN = 10            # fixtures predicted per league
PREDICT_MIN_MATCHES = 6       # matches backtest.py's and season_lab.py's
                              # MIN_PRIOR_MATCHES — both refuse a strength
                              # estimate below this many games outright. A
                              # live fixture still needs SOME number even on
                              # a season's first matchday, so _team_strengths
                              # shrinks a thin sample toward the league
                              # average instead of refusing it, in proportion
                              # to how thin it is. Found because Allsvenskan's
                              # very first 2026 matchday — one game of data
                              # per team — produced a preseason-implausible
                              # 76-point projection for Hammarby off a single
                              # result; a validated coefficient never let that
                              # sample size through in the lab code it was
                              # tuned against, only in production

# TheSportsDB fixture names -> xG-source (Understat/FotMob) names, for the
# pairs that normalisation alone cannot bridge; extended when the build
# prints an "no xG history matched" warning for a club that has history
PREDICT_ALIASES = {
    "Inter Milan": "Inter",  # bare "Inter" is a subset of both Milan clubs
    "Borussia Mönchengladbach": "Borussia M.Gladbach",
    "Köln": "FC Cologne",
    "Hamburg": "Hamburger SV",
    "RB Leipzig": "RasenBallsport Leipzig",
    "Halmstad": "Halmstads BK",
}


def _predict_norm(name):
    """Club-name join key: ASCII-folded, lowercased, generic club tokens
    dropped, remaining tokens sorted so word order does not matter."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    drop = {
        "fc", "afc", "cf", "ac", "as", "ss", "ssc", "us", "ud", "cd", "rc",
        "rcd", "sc", "sd", "sv", "bsc", "vfl", "vfb", "tsg", "fsv", "spvgg",
        "if", "ff", "bk", "sk", "fk", "aif", "club", "de", "cp", "calcio",
        "1899", "1846", "1860", "04", "05", "09", "1", "96", "98", "99",
    }
    toks = [t for t in s.split() if t not in drop]
    return " ".join(sorted(toks)) or s.strip()


def _predict_mapping(fixture_names, strength_names):
    """Fixture name -> strengths name (or None when nothing matches safely)."""
    by_norm = {_predict_norm(n): n for n in strength_names}
    tokens = {n: set(_predict_norm(n).split()) for n in strength_names}
    mapping = {}
    for name in fixture_names:
        if name in PREDICT_ALIASES:
            mapping[name] = PREDICT_ALIASES[name]
            continue
        norm = _predict_norm(name)
        if norm in by_norm:
            mapping[name] = by_norm[norm]
            continue
        want = set(norm.split())
        subset = [n for n, t in tokens.items() if want <= t or t <= want]
        mapping[name] = subset[0] if len(subset) == 1 else None
    return mapping


def _team_strengths(db, league, as_of=None):
    """Recency-weighted attack/defence strength per team — a blend of
    non-penalty xG and actual goals — plus the league's mean strength per
    team per match and its home-advantage ratio.

    as_of=None (the live default) is deliberately unchanged from before this
    parameter existed: no upper bound on match_date, so a match played
    earlier today is already counted. Passing an explicit past date is for
    the season-projection backfill, which must not leak — everything on or
    after as_of is excluded, not just down-weighted, matching the strict
    "< as_of" convention season_lab.py and backtest.py already use.
    """
    now = as_of or date.today()
    # main tables rather than the season-scoped views: the lookback window
    # deliberately crosses the season boundary (validated by backtest.py),
    # and npxG+goals is the strength definition the backtest scored best
    cutoff = (now - timedelta(days=PREDICT_LOOKBACK_DAYS)).isoformat()
    upper = " AND match_date < ?" if as_of is not None else ""
    sql = ("""SELECT team, match_date, home_away,
                    COALESCE(npxg, xg), COALESCE(npxga, xga), scored, missed, {deep}
             FROM {table}
             WHERE league = ? AND xg IS NOT NULL AND xga IS NOT NULL
               AND match_date >= ?""" + upper)
    extra = (now.isoformat(),) if as_of is not None else ()
    rows = db.execute(
        sql.format(table="main.understat_team_matches", deep="deep"),
        (league, cutoff) + extra,
    ).fetchall()
    if fotmob_available(db):
        # FotMob has no deep-completions metric — the territory term skips
        rows += db.execute(
            sql.format(table="main.fotmob_team_matches", deep="NULL"),
            (league, cutoff) + extra,
        ).fetchall()
    today = now
    teams = {}
    venue = {"h": [0.0, 0.0], "a": [0.0, 0.0]}  # weighted attack sum, weight
    lg_deep_sum, lg_deep_w = 0.0, 0.0
    for team, match_date, home_away, npxg, npxga, scored, missed, deep in rows:
        try:
            days = (today - datetime.strptime(match_date[:10], "%Y-%m-%d").date()).days
        except (TypeError, ValueError):
            continue
        w = 0.5 ** (max(days, 0) / PREDICT_HALF_LIFE_DAYS)
        attack = (1 - PREDICT_GOALS_BLEND) * npxg + PREDICT_GOALS_BLEND * (scored or 0)
        defence = (1 - PREDICT_GOALS_BLEND) * npxga + PREDICT_GOALS_BLEND * (missed or 0)
        rec = teams.setdefault(team, [0.0, 0.0, 0.0, 0, 0.0, 0.0])
        rec[0] += w * attack
        rec[1] += w * defence
        rec[2] += w
        rec[3] += 1
        if deep is not None:
            rec[4] += w * deep
            rec[5] += w
            lg_deep_sum += w * deep
            lg_deep_w += w
        if home_away in venue:
            venue[home_away][0] += w * attack
            venue[home_away][1] += w
    total_xg = venue["h"][0] + venue["a"][0]
    total_w = venue["h"][1] + venue["a"][1]
    mu = total_xg / total_w if total_w else 0.0
    home_adv = 1.0
    if venue["h"][1] and venue["a"][1] and venue["a"][0]:
        home_adv = (venue["h"][0] / venue["h"][1]) / (venue["a"][0] / venue["a"][1])
    lg_deep = lg_deep_sum / lg_deep_w if lg_deep_w else None

    strengths = {}
    for t, r in teams.items():
        if r[2] <= 0:
            continue
        att, dfn, n = r[0] / r[2], r[1] / r[2], r[3]
        if n < PREDICT_MIN_MATCHES and mu > 0:
            shrink = n / PREDICT_MIN_MATCHES
            att = shrink * att + (1 - shrink) * mu
            dfn = shrink * dfn + (1 - shrink) * mu
        strengths[t] = (att, dfn, n, (r[4] / r[5]) if r[5] else None)
    return strengths, mu, home_adv, lg_deep


def _poisson_vec(lam):
    p = [math.exp(-lam)]
    for k in range(1, PREDICT_MAX_GOALS + 1):
        p.append(p[-1] * lam / k)
    return p


def _outcome_probs(lam_home, lam_away):
    """P(home win), P(draw), P(away win)."""
    ph, pa = _poisson_vec(lam_home), _poisson_vec(lam_away)
    home = draw = away = 0.0
    for i, pi in enumerate(ph):
        for j, pj in enumerate(pa):
            p = pi * pj
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    total = home + draw + away
    return home / total, draw / total, away / total


def _fixture_lambdas(strengths, mu, home_adv, lg_deep, home, away):
    """Expected goals for one fixture, and the thinner side's sample size.

    Lifted out of predictions_block so the fixture explorer can show the
    same numbers rather than a second implementation that drifts from it —
    the two views disagreeing about the same match would be worse than
    either being slightly wrong.
    """
    att_h, def_h, n_h, deep_h = strengths[home]
    att_a, def_a, n_a, deep_a = strengths[away]
    sqrt_ha = math.sqrt(home_adv)
    lam_home = att_h * def_a / mu * sqrt_ha
    lam_away = att_a * def_h / mu / sqrt_ha
    if lg_deep and deep_h is not None and deep_a is not None:
        lam_home *= (deep_h / lg_deep) ** PREDICT_DEEP_POWER
        lam_away *= (deep_a / lg_deep) ** PREDICT_DEEP_POWER
    return (max(0.1, min(6.0, lam_home)), max(0.1, min(6.0, lam_away)),
            min(n_h, n_a))


def predictions_block(db, league):
    fixtures = db.execute(
        """SELECT match_date, round, home_team, away_team, event_id, season
           FROM matches WHERE league = ? AND home_score IS NULL AND match_date >= ?
           ORDER BY match_date, event_id LIMIT ?""",
        (league, date.today().isoformat(), PREDICT_SHOWN),
    ).fetchall()
    if not fixtures:
        return ""
    strengths, mu, home_adv, lg_deep = _team_strengths(db, league)
    if not strengths or mu <= 0:
        return ""
    names = sorted({n for _, _, h, a, _, _ in fixtures for n in (h, a)})
    mapping = _predict_mapping(names, list(strengths))
    unmatched = sorted(n for n in names if mapping.get(n) is None)
    if unmatched:
        print(f"  ! predictions ({league}): no xG history matched for: "
              + ", ".join(unmatched))

    def seg(cls, share):
        pct = f"{share * 100:.0f}%"
        label = pct if share >= 0.15 else ""
        return (f"<i class='{cls}' style='width:{share * 100:.1f}%'>{label}</i>")

    # the published calls are written down as they are made, so the report
    # card below can grade the site on what it actually said in advance
    logged = prediction_log.load()
    today = date.today().isoformat()
    tmap = _team_link_map(db, league)

    body = ""
    for match_date, rnd, home, away, event_id, season in fixtures:
        rnd_label = f"R{rnd}" if rnd else ""
        fx = f" data-fx='{escape(str(event_id))}'"
        mapped_home, mapped_away = mapping.get(home), mapping.get(away)
        if not mapped_home or not mapped_away:
            missing = home if not mapped_home else away
            body += (
                f"<tr{fx}><td class='dim'>{escape(match_date or '')}</td><td class='dim'>{rnd_label}</td>"
                f"<td style='text-align:right'>{_team_label(home, tmap)}</td>"
                f"<td class='dim' style='text-align:center'>no xG history for {escape(missing)}</td>"
                f"<td>{_team_label(away, tmap)}</td><td class='num dim'>–</td></tr>"
            )
            continue
        lam_home, lam_away, n_min = _fixture_lambdas(
            strengths, mu, home_adv, lg_deep, mapped_home, mapped_away)
        p_home, p_draw, p_away = _outcome_probs(lam_home, lam_away)
        prediction_log.record(logged, today, event_id, league, season,
                              match_date, home, away,
                              (p_home, p_draw, p_away), (lam_home, lam_away))
        tip = (f"{home} {p_home * 100:.0f}% · draw {p_draw * 100:.0f}% · "
               f"{away} {p_away * 100:.0f}% (on {n_min}+ matches each)")
        bar = (f"<div class='prob' title='{escape(tip)}'>"
               + seg("h", p_home) + seg("d", p_draw) + seg("a", p_away) + "</div>")
        body += (
            f"<tr{fx}><td class='dim'>{escape(match_date or '')}</td><td class='dim'>{rnd_label}</td>"
            f"<td style='text-align:right'>{_team_label(home, tmap)}</td>"
            f"<td style='min-width:180px'>{bar}</td>"
            f"<td>{_team_label(away, tmap)}</td>"
            f"<td class='num dim'>{lam_home:.1f}–{lam_away:.1f}</td></tr>"
        )
    prediction_log.save(logged)
    caveat = (
        "<div class='caveat'><strong>A model, not a promise.</strong> These "
        "probabilities come from a small Poisson model over each club's "
        "recency-weighted chance quality (non-penalty xG, blended with a "
        "dash of actual goals) — nothing else. It has never heard of transfers, "
        "injuries, suspensions or new managers, and until the new season "
        "produces matches it leans heavily on last season's form. Clubs with "
        "no recent top-flight xG history at all have their fixtures left "
        "unpredicted. Backtested over 21,700 matches back to 2014/15 it "
        "calls the right result 53% of the time — clearly better than "
        "always guessing home win (44%), nowhere near clairvoyant. A "
        "conversation starter — never betting advice.</div>"
    )
    legend = (
        "<p class='meta'><span class='pdot' style='background:var(--accent)'></span>home win&ensp;"
        "<span class='pdot' style='background:var(--draw)'></span>draw&ensp;"
        "<span class='pdot' style='background:var(--away)'></span>away win&ensp;·&ensp;"
        "hover a bar for the exact split</p>"
    )
    table = (
        "<div class='card'><table><thead><tr>"
        "<th>Date</th><th></th><th style='text-align:right'>Home</th>"
        "<th>Probabilities</th><th>Away</th>"
        "<th class='num' title='The model&#39;s expected goals for each side'>xG f&#39;cast</th>"
        "</tr></thead><tbody>" + body + "</tbody></table></div>"
    )
    about = (
        "Each club gets an <strong>attack</strong> and <strong>defence</strong> "
        "strength: a recency-weighted blend of 70% non-penalty xG and 30% "
        "actual goals, for and against (a match "
        f"{PREDICT_HALF_LIFE_DAYS} days old counts half as much as one today "
        "— penalties are mostly noise, while real goals carry a club's "
        "persistent finishing skill). "
        "For a fixture, the home side's expected goals are its attack scaled by "
        "the opponent's defence relative to the league average, times a "
        "home-advantage factor measured from the league's own home/away xG "
        "split — and the same, mirrored, for the visitors. Feeding both "
        "expectations through independent Poisson distributions gives a "
        "probability for every scoreline, summed into the win/draw/win split "
        "shown in the bar. <em>xG f'cast</em> is each side's expected goals — "
        "deliberately not turned into a predicted scoreline, because chance "
        "quality says little about which exact score a match lands on and "
        "even the favourite scoreline rarely tops 15%. In the "
        "Understat leagues a small territory term also scales each attack by "
        "the club's deep-completions rate relative to the league average — "
        "validated on held-out seasons, unlike pressing intensity (PPDA), "
        "which was tried the same way, carried no extra signal, and stays "
        "out. Allsvenskan's feed has no deep-completions metric, so its "
        "model simply omits the term. Strengths reach back "
        f"{PREDICT_LOOKBACK_DAYS} days — nearly four seasons — with that "
        f"{PREDICT_HALF_LIFE_DAYS}-day half-life doing the forgetting, a "
        "combination that beat a shorter hard-cutoff window on held-out "
        "seasons: an old match should fade, not fall off a cliff, because "
        "even faint evidence of how good a club is turns out to be worth "
        "keeping. Replaying every stored season with this exact recipe "
        "(backtest.py, 20,900 predictable matches) gives Brier 0.583 and "
        "53% outcome accuracy, against 0.647 and 44% for guessing by league "
        "base rates."
    )
    return block("Predictions (xG Poisson model)", caveat + legend + table, about=about)


REPORT_CARD_MIN = 30        # below this the record is pure noise, so say so
REPORT_CARD_RECENT = 8      # individual calls shown
CALIBRATION_BUCKETS = [     # (low, high, label) on the top pick's probability
    (0.00, 0.40, "under 40%"),
    (0.40, 0.50, "40–50%"),
    (0.50, 0.60, "50–60%"),
    (0.60, 0.70, "60–70%"),
    (0.70, 1.01, "over 70%"),
]


DESERVED_MIN = 10   # below this the split is noise even pooled across leagues


def deserved_comparison(db, leagues):
    """Every published call in the leagues Understat covers, scored twice:
    against what happened, and against what the chances deserved.

    Pooled across leagues on purpose. Per league the graded log stays a
    handful of matches for months, and a luck estimate drawn from a handful
    of matches is itself mostly luck; the model is the same in all five, so
    the pooled figure is the one that means anything first.

    The second score is the Brier the same call would have earned on
    average had the match been replayed from the chances both sides
    created -- an expectation over Understat's forecast, rather than the
    single 0/1 outcome that happened to occur. Both are the same statistic
    on the same scale, so the gap between them is readable: close together
    means results have been landing where the chances said they would.

    A caveat worth keeping in view: the chances a side created are not
    themselves free of luck, so this measures deviation from a better
    proxy for the truth, not from the truth.
    """
    logged = prediction_log.load()
    n = hits = agree = 0
    brier = deserved_brier = deserved_hits = 0.0
    for league in leagues:
        graded = prediction_log.graded(db, logged, league)
        if not graded:
            continue
        names = {x for row, _, _, _ in graded for x in (row["home"], row["away"])}
        lookup = _forecasts_for(db, league, names)
        for row, home_score, away_score, outcome in graded:
            fc = lookup.get((row["match_date"], row["home"], row["away"]))
            # same scoreline guard as everywhere else: a forecast that belongs
            # to a different match would quietly poison the average
            if not fc or fc[1] != home_score or fc[2] != away_score:
                continue
            q = fc[0]
            probs = prediction_log.probabilities(row)
            pick = max(range(3), key=lambda i: probs[i])
            n += 1
            hits += int(pick == outcome)
            brier += sum((p - (1.0 if i == outcome else 0.0)) ** 2
                         for i, p in enumerate(probs))
            deserved_brier += sum(
                q[j] * sum((p - (1.0 if i == j else 0.0)) ** 2
                           for i, p in enumerate(probs))
                for j in range(3)
            )
            deserved_hits += q[pick]
            agree += int(max(range(3), key=lambda i: q[i]) == outcome)
    if n < DESERVED_MIN:
        return None
    return {
        "n": n,
        "hits": hits / n * 100,
        "deserved_hits": deserved_hits / n * 100,
        "brier": brier / n,
        "deserved_brier": deserved_brier / n,
        "agree": agree / n * 100,
    }


def deserved_block(db, league):
    """The skill-or-luck half of the report card. Empty for Allsvenskan,
    whose feed has no shot-level simulation to be scored against."""
    if league not in UNDERSTAT_LEAGUES:
        return ""
    c = deserved_comparison(db, UNDERSTAT_LEAGUES)
    if not c:
        return ""
    gap = c["brier"] - c["deserved_brier"]
    if gap > 0.03:
        reading = (
            "Results have been landing worse for these calls than the chances "
            "behind them implied — on this evidence the model is reading "
            "the football better than its record shows."
        )
    elif gap < -0.03:
        reading = (
            "Results have been kinder to these calls than the chances behind "
            "them warranted — the record currently flatters the model."
        )
    else:
        reading = (
            "The two are close, which is the unexciting and healthy answer: "
            "results have landed about where the chances said they would, so "
            "the record above is being earned rather than won or lost on luck."
        )
    return (
        "<h4>Skill or luck — the same calls, scored against the chances</h4>"
        # the scope has to land before the numbers do: this is the one block on
        # a league's page that is not about that league, and read the other way
        # round its 50% looks like it should match the headline table's
        f"<p class='meta'>Not just {escape(league)}: every one of the "
        f"{c['n']} graded calls across the five leagues Understat covers, "
        "because no single league has anywhere near enough of them yet.</p>"
        "<div class='card'><table><thead><tr><th>Call scored against</th>"
        "<th class='num'>Top pick right</th><th class='num'>Brier</th>"
        "</tr></thead><tbody>"
        f"<tr><td>What happened</td><td class='num'>{c['hits']:.0f}%</td>"
        f"<td class='num'>{c['brier']:.3f}</td></tr>"
        "<tr><td>What the chances deserved</td>"
        f"<td class='num'>{c['deserved_hits']:.0f}%</td>"
        f"<td class='num'>{c['deserved_brier']:.3f}</td></tr>"
        "</tbody></table></div>"
        f"<p class='meta'>{reading} <span class='dim'>For scale, the side the "
        f"chances favoured actually won {c['agree']:.0f}% of these "
        "matches.</span></p>"
    )


def report_card_block(db, league):
    """How the published predictions have actually fared.

    Everything here is scored against predictions_log entries, i.e. calls
    written down before kickoff — never a replay of history with today's
    model. That makes the numbers small and slow to accumulate, which is
    the price of them meaning anything.
    """
    logged = prediction_log.load()
    graded = prediction_log.graded(db, logged, league)
    if not graded:
        return ""

    n = len(graded)
    hits = brier = 0.0
    early_brier = 0.0
    revised = 0
    buckets = {label: [0, 0.0, 0] for _, _, label in CALIBRATION_BUCKETS}
    for row, _, _, outcome in graded:
        revised += int(row["first_seen"] != row["last_seen"])
        probs = prediction_log.probabilities(row)
        pick = max(range(3), key=lambda i: probs[i])
        hits += int(pick == outcome)
        brier += sum((p - (1.0 if i == outcome else 0.0)) ** 2
                     for i, p in enumerate(probs))
        early = prediction_log.probabilities(row, first=True)
        early_brier += sum((p - (1.0 if i == outcome else 0.0)) ** 2
                           for i, p in enumerate(early))
        for low, high, label in CALIBRATION_BUCKETS:
            if low <= probs[pick] < high:
                buckets[label][0] += 1
                buckets[label][1] += probs[pick]
                buckets[label][2] += int(pick == outcome)
                break

    accuracy = hits / n * 100
    brier /= n
    early_brier /= n

    # only worth a row once some calls were actually revised before kickoff;
    # early on every first call is still its last and the number just repeats
    early_row = (
        f"<tr><td>Same calls, as first published <span class='dim'>"
        f"(up to two weeks earlier; {revised} of {n} were later revised)</span></td>"
        f"<td class='num'>{early_brier:.3f}</td></tr>"
    ) if revised else ""
    headline = (
        "<div class='card'><table><tbody>"
        f"<tr><td>Predictions graded</td><td class='num'>{n}</td></tr>"
        f"<tr><td>Top pick correct</td><td class='num'>{accuracy:.0f}%</td></tr>"
        f"<tr><td>Brier score <span class='dim'>(lower is better; "
        f"0.647 is guessing by league base rates)</span></td>"
        f"<td class='num'>{brier:.3f}</td></tr>"
        + early_row +
        "</tbody></table></div>"
    )

    if n < REPORT_CARD_MIN:
        note = (
            f"<div class='caveat'><strong>Far too early to mean anything.</strong> "
            f"{n} graded {'call' if n == 1 else 'calls'} is noise — a coin can "
            "look like a genius over ten matches. This table is here to fill up "
            "honestly over the season, not to be read yet.</div>"
        )
    else:
        note = (
            "<div class='caveat'><strong>A live scorecard, not the backtest.</strong> "
            "Every row here was published before kickoff and never edited, so "
            "unlike the backtest it cannot flatter the model with hindsight. "
            "Expect it to wander well above and below the backtested 53% for "
            "most of a season — a few hundred matches is still a small sample.</div>"
        )

    rows = ""
    for low, high, label in CALIBRATION_BUCKETS:
        count, prob_sum, correct = buckets[label]
        if not count:
            continue
        said = prob_sum / count * 100
        actual = correct / count * 100
        rows += (
            f"<tr><td>{label}</td><td class='num'>{count}</td>"
            f"<td class='num'>{said:.0f}%</td>"
            f"<td class='num'>{actual:.0f}%</td></tr>"
        )
    calibration = (
        "<h4>Calibration — when it says 60%, does it happen 60% of the time?</h4>"
        "<div class='card'><table><thead><tr><th>Confidence in the top pick</th>"
        "<th class='num'>Calls</th><th class='num'>Model said</th>"
        "<th class='num'>Actually happened</th></tr></thead><tbody>"
        + (rows or "<tr><td colspan='4' class='dim'>nothing graded yet</td></tr>")
        + "</tbody></table></div>"
    ) if rows else ""

    recent = ""
    for row, home_score, away_score, outcome in graded[:REPORT_CARD_RECENT]:
        probs = prediction_log.probabilities(row)
        pick = max(range(3), key=lambda i: probs[i])
        names = [row["home"], "Draw", row["away"]]
        mark = ("<span title='top pick was right'>✓</span>" if pick == outcome
                else "<span class='dim' title='top pick missed'>✗</span>")
        recent += (
            f"<tr><td class='dim'>{escape(row['match_date'])}</td>"
            f"<td style='text-align:right'>{escape(row['home'])}</td>"
            f"<td class='num'>{home_score}–{away_score}</td>"
            f"<td>{escape(row['away'])}</td>"
            f"<td class='dim'>said {escape(names[pick])} "
            f"{probs[pick] * 100:.0f}%</td>"
            f"<td class='num'>{mark}</td></tr>"
        )
    recent_table = (
        "<h4>Recent calls</h4><div class='card'><table><thead><tr>"
        "<th>Date</th><th style='text-align:right'>Home</th><th class='num'>Score</th>"
        "<th>Away</th><th>Model's top pick</th><th class='num'></th>"
        "</tr></thead><tbody>" + recent + "</tbody></table></div>"
    )

    about = (
        "Every prediction the site publishes is written to a committed log "
        "the moment it is made, and frozen the instant a result exists — so "
        "this table grades calls that were on record before kickoff. That "
        "is a stricter test than the backtest, where the model's own "
        "settings were chosen by looking at the matches it is scored on. "
        "The <em>Brier score</em> is the squared error of the whole "
        "probability split, not just the top pick: 0 is perfect, 0.647 is "
        "what guessing each league's home/draw/away base rates gets you, "
        "and the backtest average is 0.583. Calibration is the more "
        "revealing half of the table — a model can pick winners at a "
        "mediocre rate and still be well calibrated, which is what makes "
        "its probabilities usable. Once some calls have been revised "
        "between publication and kickoff, a further row re-scores the same "
        "fixtures using the first call the site ever published for them; if "
        "that number is close to the main one, the extra fortnight of data "
        "adds less than you would think. The <em>skill or luck</em> table "
        "scores the very same calls a second time, against Understat's "
        "post-match simulation of each fixture instead of against its "
        "scoreline: the Brier a call would have earned on average had the "
        "match been replayed from the chances both sides created. It is the "
        "one number here that can tell a bad model from a bad night, and it "
        "is pooled across the five leagues Understat covers because per "
        "league there are nowhere near enough graded calls to read. It is "
        "not a rival forecast — Understat's number exists only after the "
        "final whistle and knows every shot that was taken, which is exactly "
        "what makes it a fair standard to be judged against and a useless "
        "one to predict with."
    )
    return block("Model report card",
                 note + headline + calibration + recent_table
                 + deserved_block(db, league),
                 about=about)


# --------------------------------------------------- rolling season forecast

PROJECT_SIMS = 5000         # Monte Carlo seasons. Sampling error on a shown
                            # percentage peaks near 0.7pp, well inside the
                            # model's own error, and 20k was not visibly better
PROJECT_SEED = 20260809     # fixed, so a rebuild with unchanged data produces
                            # unchanged percentages — a number that jitters
                            # every night teaches the reader to distrust it
PROJECT_EUROPE = 4          # top-N highlighted, matching the standings stripe
PROJECT_RELEGATED = 3       # bottom-N ditto
PROJECT_MIN_TEAMS = 6
# A club with no usable top-flight history is not "roughly average": measured
# over the 80 such arrivals in the database (season_lab.py promoted), they
# attack at 0.79x and concede at 1.19x the league average. Treating them as
# average would put a promoted side mid-table every August.
PROJECT_PROMOTED_ATTACK = 0.787
PROJECT_PROMOTED_DEFENCE = 1.187


def _margin_sampler(lam_home, lam_away):
    """Cumulative distribution over (home goals − away goals) for one fixture.

    Only the margin is needed — it fixes both the points and the goal
    difference contribution — so one sample per fixture does the work of two
    and the simulation runs about twice as fast.
    """
    ph, pa = _poisson_vec(lam_home), _poisson_vec(lam_away)
    sh, sa = sum(ph), sum(pa)
    ph = [p / sh for p in ph]
    pa = [p / sa for p in pa]
    margins, cum, running = [], [], 0.0
    for d in range(-PREDICT_MAX_GOALS, PREDICT_MAX_GOALS + 1):
        p = sum(ph[i] * pa[i - d] for i in range(max(0, d), min(len(ph), len(pa) + d)))
        if p <= 0:
            continue
        running += p
        margins.append(d)
        cum.append(running)
    cum[-1] = 1.0  # absorb the truncated tail into the last bucket
    return cum, margins


def _projection_fixtures(db, league, season=None):
    """(season, all match rows) for one league-season. season=None finds
    the season currently in progress (the one with an unplayed fixture)."""
    if season is None:
        row = db.execute(
            """SELECT season FROM matches
               WHERE league = ? AND home_score IS NULL
               ORDER BY match_date, event_id LIMIT 1""",
            (league,),
        ).fetchone()
        if not row:
            return None
        season = row[0]
    rows = db.execute(
        """SELECT home_team, away_team, home_score, away_score, match_date
           FROM matches WHERE league = ? AND season = ?
           ORDER BY match_date, event_id""",
        (league, season),
    ).fetchall()
    if not rows:
        return None
    return season, rows


def _compute_projection(db, league, as_of=None, season=None, sims=PROJECT_SIMS):
    """The season-projection Monte Carlo, decoupled from "as of right now".

    as_of=None (the live default) means exactly what it always did: played
    = matches with a stored result, full stop. Passing a date means "pretend
    today is that day" — played = matches strictly before it, using their
    real scores (safe, since they are the past relative to that day), and
    everything from that day on is discarded and simulated fresh from
    strengths measured only on data before it — even matches that have,
    for real, been played since. That is what makes a backfilled snapshot
    honest: it can only see what the live build would have seen that night.

    Returns None when there is nothing to project, otherwise a dict with
    everything both the live HTML table and a logged snapshot need.
    """
    found = _projection_fixtures(db, league, season)
    if not found:
        return None
    season, rows = found
    if as_of is None:
        played = [r for r in rows if r[2] is not None and r[3] is not None]
        remaining = [r for r in rows if r[2] is None or r[3] is None]
        strength_as_of = None
    else:
        as_of_iso = as_of.isoformat()
        played = [r for r in rows if r[2] is not None and r[3] is not None
                  and (r[4] or "9999") < as_of_iso]
        remaining = [r for r in rows if (r[4] or "9999") >= as_of_iso]
        strength_as_of = as_of
    if not remaining:
        return None

    teams = sorted({t for h, a, _, _, _ in played + remaining for t in (h, a)})
    if len(teams) < PROJECT_MIN_TEAMS:
        return None
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    base_pts, base_gd, base_played = [0] * n, [0] * n, [0] * n
    base_gf, base_ga = [0] * n, [0] * n
    base_w, base_d, base_l = [0] * n, [0] * n, [0] * n
    for home, away, hs, as_, _ in played:
        h, a = idx[home], idx[away]
        base_played[h] += 1
        base_played[a] += 1
        base_gd[h] += hs - as_
        base_gd[a] += as_ - hs
        base_gf[h] += hs
        base_ga[h] += as_
        base_gf[a] += as_
        base_ga[a] += hs
        if hs > as_:
            base_pts[h] += 3
            base_w[h] += 1
            base_l[a] += 1
        elif hs == as_:
            base_pts[h] += 1
            base_pts[a] += 1
            base_d[h] += 1
            base_d[a] += 1
        else:
            base_pts[a] += 3
            base_w[a] += 1
            base_l[h] += 1

    strengths, mu, home_adv, lg_deep = _team_strengths(db, league, as_of=strength_as_of)
    if not strengths or mu <= 0:
        return None
    mapping = _predict_mapping(teams, list(strengths))
    sqrt_ha = math.sqrt(home_adv)
    promoted = (PROJECT_PROMOTED_ATTACK * mu, PROJECT_PROMOTED_DEFENCE * mu,
                0, None)
    n_promoted = sum(1 for t in teams if mapping.get(t) is None)

    prepared = []
    fixture_lambdas = []
    for home, away, _, _, match_date in remaining:
        att_h, def_h, _, deep_h = strengths.get(mapping.get(home)) or promoted
        att_a, def_a, _, deep_a = strengths.get(mapping.get(away)) or promoted
        lam_home = att_h * def_a / mu * sqrt_ha
        lam_away = att_a * def_h / mu / sqrt_ha
        if lg_deep and deep_h is not None and deep_a is not None:
            lam_home *= (deep_h / lg_deep) ** PREDICT_DEEP_POWER
            lam_away *= (deep_a / lg_deep) ** PREDICT_DEEP_POWER
        lam_home = max(0.1, min(6.0, lam_home))
        lam_away = max(0.1, min(6.0, lam_away))
        cum, margins = _margin_sampler(lam_home, lam_away)
        prepared.append((cum, margins, idx[home], idx[away]))
        fixture_lambdas.append((idx[home], idx[away], round(lam_home, 3),
                                round(lam_away, 3), match_date))

    rng = random.Random(PROJECT_SEED)
    rand = rng.random
    pts_total = [0] * n
    title = [0] * n
    europe = [0] * n
    drop = [0] * n
    rank_counts = [[0] * n for _ in range(n)]
    rel_cut = n - PROJECT_RELEGATED
    for _ in range(sims):
        pts, gd = base_pts[:], base_gd[:]
        for cum, margins, h, a in prepared:
            d = margins[bisect(cum, rand())]
            gd[h] += d
            gd[a] -= d
            if d > 0:
                pts[h] += 3
            elif d == 0:
                pts[h] += 1
                pts[a] += 1
            else:
                pts[a] += 3
        # ties on points and goal difference are broken at random rather than
        # by goals scored: the sim never generates a scoreline, only a margin
        order = sorted(range(n), key=lambda i: (pts[i], gd[i], rand()),
                       reverse=True)
        for rank, i in enumerate(order):
            pts_total[i] += pts[i]
            rank_counts[i][rank] += 1
            if rank == 0:
                title[i] += 1
            if rank < PROJECT_EUROPE:
                europe[i] += 1
            if rank >= rel_cut:
                drop[i] += 1

    proj = [pts_total[i] / sims for i in range(n)]
    started = any(base_played)
    now_rank = {}
    if started:
        standing = sorted(range(n), key=lambda i: (base_pts[i], base_gd[i]),
                          reverse=True)
        now_rank = {i: r for r, i in enumerate(standing, 1)}
    order = sorted(range(n), key=lambda i: (proj[i], base_gd[i]), reverse=True)

    return {
        "season": season, "teams": teams, "n": n, "sims": sims,
        "n_played": len(played), "n_remaining": len(remaining),
        "n_promoted": n_promoted, "base_pts": base_pts, "base_gd": base_gd,
        "base_played": base_played, "base_gf": base_gf, "base_ga": base_ga,
        "base_w": base_w, "base_d": base_d, "base_l": base_l,
        "proj": proj, "title": title,
        "europe": europe, "drop": drop, "started": started,
        "now_rank": now_rank, "order": order, "rank_counts": rank_counts,
        "fixture_lambdas": fixture_lambdas,
    }


def _history_depth(db, league, season, as_of=None):
    """(days, season_only) describing how much history feeds this league's
    strengths. days is the gap between as_of and the oldest match on
    record — how much of the model's intended 1400-day window it's
    actually getting. season_only is True when that oldest match falls
    inside the season being projected, i.e. there is no PRIOR season on
    file at all, not just a thin one — the plainer, more useful thing to
    say when it's true, since "the model's history goes back 128 days" asks
    a reader to do arithmetic that "built from this season alone" doesn't.

    Every league now has several seasons on file and is nowhere near either
    condition. Allsvenskan used to trip both, holding 2026 alone until
    fetch_fotmob.py was backfilled to 2023 — which is worth keeping in mind
    when adding a league, because nothing else complains: the model quietly
    shrinks every club to the league average and projects the whole table to
    the same points, and only this pair of flags says why."""
    now = as_of or date.today()
    oldest = db.execute(
        "SELECT MIN(match_date) FROM main.understat_team_matches WHERE league = ?",
        (league,),
    ).fetchone()[0]
    if fotmob_available(db):
        alt = db.execute(
            "SELECT MIN(match_date) FROM main.fotmob_team_matches WHERE league = ?",
            (league,),
        ).fetchone()[0]
        if alt and (not oldest or alt < oldest):
            oldest = alt
    if not oldest:
        return None, False
    try:
        d = datetime.strptime(oldest[:10], "%Y-%m-%d").date()
    except ValueError:
        return None, False
    season_start = db.execute(
        "SELECT MIN(match_date) FROM main.matches WHERE league = ? AND season = ?",
        (league, season),
    ).fetchone()[0]
    season_only = bool(season_start and oldest >= season_start)
    return (now - d).days, season_only


def season_projection_block(db, league):
    """Where the season is heading, re-read from scratch every night.

    The projection is deliberately NOT an extrapolation of the table. Points
    already banked are kept exactly as they are — they are real and cannot be
    taken away — but every fixture still to be played is simulated from the
    same recency-weighted xG strengths the match predictions use. So a club
    riding a hot finishing streak keeps its points and is still projected on
    the chances it actually creates, and a good side sitting eleventh is
    projected to climb.

    Validated in season_lab.py by replaying 58 completed seasons and
    projecting the final table at nine points in each: mean absolute error
    7.7 points after a tenth of the season against 18.8 for extrapolating
    the table, and it wins at every checkpoint through to the last
    (paired t across held-out seasons −17.9 to −3.9).
    """
    r = _compute_projection(db, league)
    if not r:
        return ""
    season, teams, n, sims = r["season"], r["teams"], r["n"], r["sims"]
    proj, title, europe, drop = r["proj"], r["title"], r["europe"], r["drop"]
    base_pts, base_played = r["base_pts"], r["base_played"]
    now_rank, order, started = r["now_rank"], r["order"], r["started"]
    n_promoted = r["n_promoted"]
    rel_cut = n - PROJECT_RELEGATED
    history_days, season_only = _history_depth(db, league, season)
    thin_history = history_days is not None and history_days < PREDICT_LOOKBACK_DAYS * 0.5

    # every build logs today's numbers, so the trend chart below has
    # tomorrow's history to draw from; a same-day rerun just overwrites
    logged = projection_log.load()
    projection_log.record_snapshot(logged, date.today().isoformat(), league,
                                   season, teams, proj, title, europe, drop, sims)
    projection_log.save(logged)

    def pcell(count, cls):
        share = count / sims
        if share < 0.005:
            return "<td class='num dim'>–</td>"
        return (f"<td class='num pcell'><i class='{cls}' "
                f"style='width:{share * 100:.0f}%'></i>"
                f"<span>{share * 100:.0f}%</span></td>")

    body = ""
    for rank, i in enumerate(order, 1):
        zone = (" class='zone-cl'" if rank <= PROJECT_EUROPE
                else " class='zone-rel'" if rank > rel_cut else "")
        if started:
            # positive = projected to finish above where the club sits today
            now = (f"<td class='num dim'>{now_rank[i]}</td>"
                   f"<td class='num'>{trend_arrow(now_rank[i] - rank)}</td>"
                   f"<td class='num'>{base_played[i]}</td>"
                   f"<td class='num score'>{base_pts[i]}</td>")
        else:
            now = ""
        body += (
            f"<tr{zone}><td class='num'>{rank}</td><td>{escape(teams[i])}</td>"
            + now
            + f"<td class='num score'>{proj[i]:.0f}</td>"
            + pcell(title[i], "win") + pcell(europe[i], "cl")
            + pcell(drop[i], "rel") + "</tr>"
        )

    head_now = ("<th class='num'>Now</th><th></th><th class='num'>P</th>"
                "<th class='num'>Pts</th>") if started else ""
    table = (
        "<div class='card'><table><thead><tr>"
        "<th class='num'>#</th><th>Team</th>" + head_now
        + "<th class='num' title='Mean final points over "
        f"{sims:,} simulated seasons'>Proj</th>"
        "<th class='num'>Title</th>"
        f"<th class='num'>Top {PROJECT_EUROPE}</th>"
        f"<th class='num'>Bottom {PROJECT_RELEGATED}</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )

    if started:
        lead = (f"{r['n_remaining']} of {r['n_played'] + r['n_remaining']} "
                "matches left to play. Points already won are kept as they "
                "are; every remaining fixture is simulated from current xG "
                "form.")
    else:
        lead = ("The season has not kicked off, so this is last season's "
                "evidence and nothing else — it will start moving with the "
                "first results.")
    note = f"<p class='meta'>{escape(lead)}</p>"

    caveat = (
        "<div class='caveat'><strong>Form, not news.</strong> The simulation "
        "knows only what clubs have done on the pitch. It has never heard of "
        "a transfer window, a sacking or an injury — when those things "
        "matter, they reach the projection the slow way, through results. "
        "It also assumes each club's current level holds for the rest of the "
        "season, which is exactly the assumption a January collapse breaks."
        + (f" {n_promoted} newly promoted club"
           f"{'s have' if n_promoted != 1 else ' has'} no top-flight history "
           "at all here, and start from the average record of promoted sides "
           "rather than from anything about this particular squad."
           if n_promoted else "")
        + " A club returning after a year in the division below is projected "
        "on the last top-flight football it played, which may be a squad ago. "
        "A club scoring far more than its chances suggest is only partly "
        "credited for it — usually the right call, since across 1,170 "
        "historical team-seasons that kind of overperformance mostly "
        "evaporates, but the extreme cases (Sirius-scale runs included) "
        "regressed to roughly half their gap rather than to zero, and the "
        "model's discount was never tuned specifically for that tail."
        + (" And these strengths are built from this season's matches "
           "alone — no earlier season is in the database for this league "
           "yet — where the big five get up to four seasons of recency-"
           "weighted history behind the same model."
           if season_only else
           f" And this league's own history only goes back {history_days} "
           "days, well short of the model's intended four seasons — its "
           "strengths are working with less than the model was built for."
           if thin_history else "")
        + "</div>"
    )
    about = (
        "<p><strong>What it does.</strong> Every fixture left in the season is "
        f"played out {PROJECT_SIMS:,} times using the same Poisson model as "
        "the predictions above, and the resulting final tables are counted "
        "up. <em>Proj</em> is the average final points total; the three "
        "percentages are how often a club finished first, in the top "
        f"{PROJECT_EUROPE}, and in the bottom {PROJECT_RELEGATED}.</p>"
        "<p><strong>Why it disagrees with the table.</strong> Points already "
        "banked are carried over untouched — they are real. Everything still "
        "to come is projected from recency-weighted non-penalty xG, which is "
        "mostly deaf to a hot finishing run. So a club sitting third on a "
        "thin xG record gets to keep its points and is still projected to "
        "fade, while a good side stuck in mid-table is projected to climb. "
        "The gap between the <em>Now</em> and <em>#</em> columns is that "
        "disagreement, in places.</p>"
        "<p><strong>Does it work?</strong> season_lab.py replays 58 finished "
        "seasons and projects the final table at ten points in each. Before "
        "a ball is kicked it lands 8.2 points per club from the final total; "
        "after a tenth of a season, 7.7 — against 18.8 for reading the table "
        "and multiplying, which at that stage is worse than simply assuming "
        "every club finishes on the league average. The model is ahead at "
        "every checkpoint right through to the final tenth, on held-out "
        "seasons its settings were never tuned on. Shrinking it back toward "
        "the table was tried and made it worse at every weight. Worth "
        "knowing: the preseason projection is already most of the way there. "
        "Updating it as results arrive is worth about 0.2 points per club "
        "after a tenth of the season, peaks near 0.7 at halfway, and is back "
        "under 0.2 by the run-in — most of what this table knows, it knew "
        "in August.</p>"
        "<p><strong>Reading the percentages.</strong> They are simulation "
        "frequencies, not certainties, and the top-four and bottom-three "
        "cuts are the site's own convention — real European and relegation "
        "places vary by league and by season. Anything under 0.5% shows as "
        "a dash.</p>"
    )
    return block(f"Season projection ({escape(str(season))})",
                 caveat + note + table, about=about)


PROJECT_TREND_MIN_DATES = 3   # fewer and it is two dots joined by a straight
                              # line, not a trend — wait for a real one
PROJECT_TREND_MIN_SPAN = 2    # points; a panel's own range is floored here so
                              # sub-point noise doesn't get stretched into a
                              # shape that looks like real movement


def season_projection_trend(db, league):
    """The projection over time — the payoff of logging a snapshot nightly.

    A single night's table is a snapshot; this is the film behind it, built
    from projection_log.py's committed history. Same house style as the
    rolling xG sparklines below (one small panel per team, a shared scale):
    color here marks direction, not identity — whether a club's own
    projection has risen or fallen since its first logged snapshot, never a
    comparison between two different teams' lines — so no categorical
    palette or legend key is needed, only the green/red already used
    everywhere else on the site for a plain up/down.
    """
    live = _compute_projection(db, league)
    if not live:
        return ""
    season = live["season"]
    logged = projection_log.load()
    series = projection_log.series(logged, league, season)
    n_dates = len({r["date"] for r in logged.values()
                  if r["league"] == league and r["season"] == season})
    if n_dates < PROJECT_TREND_MIN_DATES:
        return ""

    order = [live["teams"][i] for i in live["order"] if live["teams"][i] in series]
    w, h = 220, 64

    # each panel scaled to its OWN range, not a shared one: unlike the xG
    # sparklines below, points has no natural shared reference point (no
    # zero line separating good from bad), so a common scale would just
    # flatten every mid-table club into a visually dead line while Sirius
    # dominates the vertical space — exactly backwards, since the delta
    # badge already carries the cross-team comparison and this chart's job
    # is showing THIS club's own swings, however small
    cells = []
    for idx, team in enumerate(order):
        values = series[team]
        if len(values) < 2:
            continue
        own = [v[1] for v in values]
        lo, hi = min(own), max(own)
        # a floor under the displayed range, not just under an exactly-zero
        # one: Sassuolo's real span here is 0.1 points (Monte Carlo re-seed
        # noise between nightly builds, nothing having actually happened),
        # and stretching a 0.1-point wobble to fill the panel drew a
        # dramatic-looking dive out of noise smaller than a single sim's
        # margin of error — every real Allsvenskan club's smallest span
        # once its season is underway is 10+ points, so 2 comfortably
        # separates "hasn't moved" from "has"
        if hi - lo < PROJECT_TREND_MIN_SPAN:
            mid = (lo + hi) / 2
            lo, hi = mid - PROJECT_TREND_MIN_SPAN / 2, mid + PROJECT_TREND_MIN_SPAN / 2
        pad = (hi - lo) * 0.15

        def y_of(v, lo=lo - pad, hi=hi + pad):
            return h - 6 - (v - lo) / (hi - lo) * (h - 12)

        step = w / (len(values) - 1)
        pts = [(i * step, y_of(v[1])) for i, v in enumerate(values)]
        points = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        delta = values[-1][1] - values[0][1]
        # round before classifying: raw Monte Carlo noise can put an
        # unstarted season's delta a hair below zero, and Python's "+.0f"
        # keeps the minus sign on a negative float even when it rounds to
        # 0 (f"{-0.3:+.0f}" == "-0") — round() on a float drops it, so
        # rounding first keeps a team that's displayed as "+0 pts" from
        # also getting a red "down" line for the same non-move
        delta_r = round(delta)
        sign = "up" if delta_r >= 0 else "down"
        val_cls = "pos" if delta_r > 0 else "neg" if delta_r < 0 else "dim"
        dots = "".join(
            f"<circle class='spark-dot {sign}' cx='{x:.1f}' cy='{y:.1f}' r='1.7'>"
            f"<title>{escape(v[0])}: {v[1]:.0f} proj pts · title {v[2] * 100:.0f}% "
            f"· top {PROJECT_EUROPE} {v[3] * 100:.0f}% · bottom "
            f"{PROJECT_RELEGATED} {v[4] * 100:.0f}%</title></circle>"
            for (x, y), v in zip(pts, values)
        )
        cells.append(
            f"<div class='spark'><p class='name'><span class='rank'>{idx + 1}</span> "
            f"{escape(team)}<span class='val {val_cls}'>{fmt_delta(delta_r, 0)} pts</span></p>"
            f"<svg viewBox='0 0 {w} {h}' width='100%' role='img' "
            f"aria-label='{escape(team)} projected final points over the season'>"
            f"<title>{escape(team)}: projected final points, {values[0][1]:.0f} on "
            f"{escape(values[0][0])} to {values[-1][1]:.0f} on {escape(values[-1][0])}</title>"
            f"<polyline class='spark-line {sign}' points='{points}'/>"
            f"{dots}"
            f"<circle class='spark-dot {sign}' cx='{pts[-1][0]:.1f}' cy='{pts[-1][1]:.1f}' r='3'/>"
            "</svg></div>"
        )
    if not cells:
        return ""

    legend = (
        f"<p class='spark-legend'>One panel per team, today's projected order · "
        f"{n_dates} nightly snapshots logged so far · each panel is scaled to "
        f"its own range, so a small club's swings are as visible as a title "
        f"contender's — read the badge, not the height, for the size of the "
        f"move · <span class='pos'>green</span> = projection has risen since "
        f"its first snapshot, <span class='neg'>red</span> = fallen · hover "
        f"a dot for that night's title / European / relegation odds</p>"
    )
    chart = f"<div class='chart-card'>{legend}<div class='spark-grid'>{''.join(cells)}</div></div>"
    about = (
        "<p><strong>What it shows.</strong> The season projection above is "
        "recomputed from scratch on every build, so on its own the page can "
        "only ever show tonight's opinion. This is every previous night's "
        "opinion too — one point added per team per night the projection "
        "ran, going back to the first snapshot logged this season.</p>"
        "<p><strong>Reading it.</strong> The number is projected final "
        "points, not the live table — it moves for two different reasons "
        "that look identical on the curve: a result banking real points, or "
        "the model simply revising its opinion of a club's remaining "
        "fixtures without a ball being kicked. Hover any dot for that "
        "night's title, European and relegation odds, which move for the "
        "same two reasons.</p>"
        "<p><strong>Why not just show probabilities.</strong> Title, "
        "European and relegation odds are usually a flat 0% or 100% for "
        "most of a season for most clubs — a true story for a handful of "
        "teams and a flat line for everyone else. Projected points moves "
        "for every club, contending or not, which is why it is the line "
        "and the odds are the hover.</p>"
    )
    return block("Projection over time", chart, about=about)


ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def ordinal(n):
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ORDINAL_SUFFIX.get(n % 10, 'th')}"


def _rank_percentile(counts, sims, q):
    """The rank at which the qth fraction of simulated seasons has finished
    at-or-above — i.e. counts (indexed by 0-based rank) turned into a
    1-based rank boundary. Walks the histogram rather than sorting sims
    individually since sims are already collapsed into per-rank counts."""
    target = q * sims
    c = 0
    for r, cnt in enumerate(counts):
        c += cnt
        if c >= target:
            return r + 1
    return len(counts)


def season_projection_distribution(db, league):
    """The spread hiding behind each club's single Proj number.

    Two clubs can share a Proj of 55 for very different reasons — one of
    them nailed on for exactly there, the other anywhere from 7th to
    relegation depending how a handful of close matches go. This reads
    that spread straight off the same simulations the table above already
    ran, so it costs nothing new to validate: it is the existing,
    already-checked projection, just not collapsed to a single number.
    """
    r = _compute_projection(db, league)
    if not r:
        return ""
    teams, n, sims = r["teams"], r["n"], r["sims"]
    rank_counts, order = r["rank_counts"], r["order"]
    if n < 3:
        return ""
    rel_cut = n - PROJECT_RELEGATED
    step = 100 / (n - 1)

    def x(rank):
        return (rank - 1) * step

    def zone(lo, hi):
        left = max(0.0, x(lo) - step / 2)
        right = min(100.0, x(hi) + step / 2)
        return left, right - left

    title_l, title_w = zone(1, 1)
    cl_l, cl_w = zone(1, PROJECT_EUROPE)
    rel_l, rel_w = zone(rel_cut + 1, n)

    rows = []
    for pos, i in enumerate(order, 1):
        p05, p25, p50, p75, p95 = (
            _rank_percentile(rank_counts[i], sims, q)
            for q in (0.05, 0.25, 0.5, 0.75, 0.95)
        )
        tip = (f"{teams[i]}: median finish {ordinal(p50)} · half of all "
               f"simulations finish {ordinal(p25)}–{ordinal(p75)} · 90% "
               f"finish {ordinal(p05)}–{ordinal(p95)}")
        wl, ww = x(p05), x(p95) - x(p05)
        bl, bw = x(p25), x(p75) - x(p25)
        rows.append(
            "<div class='range-row'>"
            f"<span class='range-name'><b>{pos}</b> {escape(teams[i])}</span>"
            f"<div class='range-track' title='{escape(tip)}'>"
            f"<i class='rz win' style='left:{title_l:.2f}%;width:{title_w:.2f}%'></i>"
            f"<i class='rz cl' style='left:{cl_l:.2f}%;width:{cl_w:.2f}%'></i>"
            f"<i class='rz rel' style='left:{rel_l:.2f}%;width:{rel_w:.2f}%'></i>"
            f"<i class='range-whisker' style='left:{wl:.2f}%;width:{ww:.2f}%'></i>"
            f"<i class='range-box' style='left:{bl:.2f}%;width:{max(bw, 0.6):.2f}%'></i>"
            f"<i class='range-median' style='left:{x(p50):.2f}%'></i>"
            "</div></div>"
        )

    legend = (
        "<p class='spark-legend'>One row per team, today's projected order · "
        "faint band = top 1 / top "
        f"{PROJECT_EUROPE} / bottom {PROJECT_RELEGATED} finish zones · thick "
        "bar = the middle 50% of simulated finishes, thin line = the middle "
        "90%, tick = median · hover a row for the exact numbers</p>"
    )
    chart = f"<div class='chart-card'>{legend}<div class='range-grid'>{''.join(rows)}</div></div>"
    about = (
        "<p><strong>What it shows.</strong> The same "
        f"{sims:,} simulated seasons behind the Proj column above, but kept "
        "as a distribution of finishing positions instead of averaged down "
        "to one number. A short bar means the simulations agree; a long "
        "one means the run-in is still wide open for that club.</p>"
        "<p><strong>Reading it.</strong> The thick bar covers the middle "
        "half of simulated outcomes, the thin line the middle 90% — so a "
        "club whose thick bar sits entirely inside the shaded relegation "
        "band is in real trouble, while one whose bar straddles the line "
        "is still fighting it. The tick is the median finish, which is "
        "usually close to but not identical to the rounded Proj points "
        "figure above (points and rank are different simulation outputs).</p>"
    )
    return block("How wide is that projection?", chart, about=about)


def _poisson_js():
    """Client-side Poisson sampler + single-season roll, shared by every
    league's simulate button — kept as one script tag rather than one per
    league since the logic is identical, only the embedded fixture data
    differs."""
    return """
(function () {
  function poissonSample(lam) {
    var L = Math.exp(-lam), k = 0, p = 1;
    do { k++; p *= Math.random(); } while (p > L);
    return k - 1;
  }
  function simulate(data) {
    var n = data.teams.length;
    var pts = data.basePts.slice(), gd = data.baseGd.slice();
    var gf = data.baseGf.slice(), ga = data.baseGa.slice();
    var w = data.baseW.slice(), d = data.baseD.slice(), l = data.baseL.slice();
    var played = data.basePlayed.slice();
    var results = [];
    data.fixtures.forEach(function (fx) {
      var h = fx[0], a = fx[1], hg = poissonSample(fx[2]), ag = poissonSample(fx[3]);
      played[h]++; played[a]++;
      gf[h] += hg; ga[h] += ag; gf[a] += ag; ga[a] += hg;
      gd[h] += hg - ag; gd[a] += ag - hg;
      if (hg > ag) { pts[h] += 3; w[h]++; l[a]++; }
      else if (hg === ag) { pts[h]++; pts[a]++; d[h]++; d[a]++; }
      else { pts[a] += 3; w[a]++; l[h]++; }
      results.push({ h: h, a: a, hg: hg, ag: ag, date: fx[4] });
    });
    var order = Array.from({ length: n }, function (_, i) { return i; });
    order.sort(function (x, y) {
      return pts[y] - pts[x] || gd[y] - gd[x] || gf[y] - gf[x] ||
        data.teams[x].localeCompare(data.teams[y]);
    });
    return { pts: pts, gd: gd, gf: gf, ga: ga, w: w, d: d, l: l, played: played,
              order: order, results: results };
  }
  function esc(s) {
    return String(s).replace(/[&<>]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c];
    });
  }
  function render(card, data) {
    var sim = simulate(data);
    var n = data.teams.length, relCut = n - data.relegated;
    var rows = sim.order.map(function (i, pos) {
      var rank = pos + 1;
      var zone = rank <= data.europe ? " class='zone-cl'" :
        rank > relCut ? " class='zone-rel'" : "";
      return "<tr" + zone + "><td class='num'>" + rank + "</td><td>" + esc(data.teams[i]) +
        "</td><td class='num'>" + sim.played[i] + "</td><td class='num'>" + sim.w[i] +
        "</td><td class='num'>" + sim.d[i] + "</td><td class='num'>" + sim.l[i] +
        "</td><td class='num dim'>" + sim.gf[i] + "–" + sim.ga[i] +
        "</td><td class='num'>" + (sim.gd[i] >= 0 ? "+" : "") + sim.gd[i] +
        "</td><td class='num score'>" + sim.pts[i] + "</td></tr>";
    }).join("");
    var table = "<div class='card'><table><thead><tr><th class='num'>#</th><th>Team</th>" +
      "<th class='num'>P</th><th class='num'>W</th><th class='num'>D</th><th class='num'>L</th>" +
      "<th class='num'>GF–GA</th><th class='num'>GD</th><th class='num'>Pts</th></tr></thead>" +
      "<tbody>" + rows + "</tbody></table></div>";
    var byDate = sim.results.slice().sort(function (a, b) {
      return (a.date || "").localeCompare(b.date || "");
    });
    var fixtures = byDate.map(function (m) {
      return "<li>" + (m.date ? "<span class='dim'>" + esc(m.date) + "</span> " : "") +
        esc(data.teams[m.h]) + " <b>" + m.hg + "–" + m.ag + "</b> " +
        esc(data.teams[m.a]) + "</li>";
    }).join("");
    var out = card.querySelector(".sim-output");
    out.innerHTML = table +
      "<details class='about'><summary>" + sim.results.length +
      " simulated results</summary><div class='about-body'><ul class='sim-fixtures'>" +
      fixtures + "</ul></div></details>";
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".sim-btn");
    if (!btn) return;
    var card = btn.closest(".sim-card");
    if (!card) return;
    var dataEl = card.querySelector(".sim-data");
    var data = JSON.parse(dataEl.textContent);
    render(card, data);
    btn.textContent = "Simulate again";
  });
})();
"""


def season_projection_simulator(db, league):
    """One random full season, played out once instead of averaged.

    The table and distribution above summarize thousands of simulations;
    this runs exactly one, all client-side, from the same per-fixture
    expected-goals numbers the Monte Carlo already computed server-side —
    no new model, just a single draw from it shown as an actual scoreline
    per match instead of a probability. Not a prediction on its own (any
    one draw is just as likely to be wrong in either direction as the
    average is to be right); it exists to make the shape of the season's
    remaining uncertainty concrete rather than statistical.
    """
    r = _compute_projection(db, league)
    if not r or not r["fixture_lambdas"]:
        return ""
    payload = {
        "teams": r["teams"], "basePts": r["base_pts"], "baseGd": r["base_gd"],
        "baseGf": r["base_gf"], "baseGa": r["base_ga"], "baseW": r["base_w"],
        "baseD": r["base_d"], "baseL": r["base_l"], "basePlayed": r["base_played"],
        "fixtures": r["fixture_lambdas"], "europe": PROJECT_EUROPE,
        "relegated": PROJECT_RELEGATED,
    }
    payload_json = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    body = (
        "<div class='card sim-card'>"
        "<div class='controls'><button class='sim-btn' type='button'>"
        "Simulate one season</button>"
        "<span class='count dim'>Plays every remaining fixture once, using "
        "the same expected-goals numbers as the projection above.</span></div>"
        "<div class='sim-output'></div>"
        f"<script type='application/json' class='sim-data'>{payload_json}</script>"
        "</div>"
    )
    about = (
        "<p><strong>What it does.</strong> Click the button and every "
        "fixture still to be played gets one simulated scoreline — each "
        "team's goals drawn from a Poisson distribution around the same "
        "expected-goals number (<code>lam_home</code>/<code>lam_away</code>) "
        "used everywhere else on this page — then the final table is built "
        "from real goals this time, not just win/draw/loss margins, so "
        "goal difference and the goals column are genuine tiebreakers "
        "rather than a coin flip. Click again for a different one.</p>"
        "<p><strong>Why it won't match the projection above.</strong> The "
        "Proj table is the average of thousands of these; any single run "
        "can and will put a mid-table side in the title race or drop a "
        "contender to mid-table, the same way any one real season can. "
        "That's the point of running it once instead of a thousand times — "
        "it shows what a plausible individual outcome actually looks like, "
        "not just the odds of it.</p>"
    )
    return block("Simulate one season", body, about=about)


# ------------------------------------------------------- understat sections

def xg_table(db, league):
    rows = db.execute(
        """SELECT team, COUNT(*), SUM(pts), SUM(xpts), SUM(scored), SUM(missed),
                  SUM(xg), SUM(xga), SUM(npxgd)
           FROM understat_team_matches WHERE league = ?
           GROUP BY team ORDER BY SUM(pts) DESC, SUM(xpts) DESC, team""",
        (league,),
    ).fetchall()
    if not rows:
        return ""
    body = ""
    for rank, (team, games, pts, xpts, gf, ga, xg, xga, npxgd) in enumerate(rows, 1):
        luck = pts - xpts
        body += (
            f"<tr><td class='num'>{rank}</td>"
            f"<td>{_analytics_label(team)}</td>"
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
    hint = ("<p class='meta team-hint' hidden>Click a club to load it in the "
            "comparison below.</p>")
    return block("xG table — results vs expected", hint + card, about)


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
        "GROUP BY team ORDER BY SUM(pts) DESC, team", (league,)
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
        # one small dot per matchday so single matches are visible on the curve;
        # rolling index i covers the window ending at matchday ROLLING_WINDOW + i
        dots = "".join(
            f"<circle class='spark-dot {'up' if v >= 0 else 'down'}' "
            f"cx='{x:.1f}' cy='{y:.1f}' r='1.7'>"
            f"<title>matchday {ROLLING_WINDOW + i}: {fmt_delta(v, 2)}</title></circle>"
            for i, (v, (x, y)) in enumerate(zip(values, pts))
        )
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
            f"{dots}"
            f"<circle class='spark-dot {sign}' cx='{pts[-1][0]:.1f}' cy='{pts[-1][1]:.1f}' r='3'/>"
            "</svg></div>"
        )
    legend = (
        f"<p class='spark-legend'>One panel per team, final-table order · each runs "
        f"matchday {ROLLING_WINDOW} → {n_matches}, the faint vertical line is "
        f"mid-season · <span class='pos'>green above zero</span> = out-creating "
        f"opponents, <span class='neg'>red below</span> = out-created · all panels "
        f"share the same ±{max_abs:.1f} scale · one dot per matchday (hover for its "
        f"value), the big dot and number = latest {ROLLING_WINDOW}-match window</p>"
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
            f"<tr{zone}><td class='num'>{xrank}</td>"
            f"<td>{_analytics_label(team)}</td>"
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
    hint = ("<p class='meta team-hint' hidden>Click a club to open it in "
            "Team analytics.</p>")
    return block("The justice table — where the chances say you belonged",
                 hint + card, about)


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
    # early in a season some teams have only played home or only away
    # fixtures so far -- AVG() over the empty side returns NULL, and the
    # venue-edge comparison needs both sides sampled anyway
    rows = [r for r in rows if r[1] is not None and r[2] is not None]
    if not rows:
        return ""
    ranked = sorted(rows, key=lambda r: r[1] - r[2], reverse=True)
    shown = ranked[:limit // 2] + ranked[-limit // 2:]
    body = ""
    for team, home, away in shown:
        body += (
            f"<tr><td>{_analytics_label(team)}</td>"
            f"<td class='num'>{fmt_delta_html(home, 2)}</td>"
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
    hint = ("<p class='meta team-hint' hidden>Click a club to open it in "
            "Team analytics.</p>")
    return block("Venue dependence — who's a different team on the road",
                 hint + card, about)


def shot_diet_scatter(db, league, top_shooters=30, min_minutes=900):
    """Shot volume vs average chance quality for the league's main shooters."""
    rows = db.execute(
        """SELECT player_name, team, minutes, shots, npg, npxg FROM understat_players
           WHERE league = ? AND minutes >= ? AND shots > 0
           ORDER BY shots DESC, player_name LIMIT ?""",
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
           ORDER BY xg_buildup * 90.0 / minutes DESC, player_name LIMIT ?""",
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
           ORDER BY goals - npg DESC, goals DESC, player_name LIMIT ?""",
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
        f"<h2>Insights <span class='dim'>({sources_label(db, leagues)})</span></h2>"
        "<p class='meta'>Second-order reads of the xG data: what the raw tables hide.</p>"
        + metric_glossary() + views
    )


# ------------------------------------------------------ best of europe tab

def europe_attackers_table(db, limit=25, min_minutes=1350):
    rows = db.execute(
        """SELECT player_name, team, league, minutes, npg, assists, npxg, xa,
                  (npxg + xa) * 90.0 / minutes AS threat
           FROM understat_players WHERE minutes >= ?
           ORDER BY threat DESC, player_name LIMIT ?""",
        (min_minutes, limit),
    ).fetchall()
    if not rows:
        return ""
    body = ""
    for rank, (name, team, lg, minutes, npg, assists, npxg, xa, threat) in enumerate(rows, 1):
        body += (
            f"<tr><td class='num'>{rank}</td><td>{escape(unescape(name))}</td>"
            f"<td class='dim'>{escape(unescape(team))}</td><td class='dim'>{escape(lg)}</td>"
            f"<td class='num'>{minutes}</td>"
            f"<td class='num'>{npxg * 90 / minutes:.2f}</td>"
            f"<td class='num'>{xa * 90 / minutes:.2f}</td>"
            f"<td class='num score'>{threat:.2f}</td>"
            f"<td class='num'>{npg}+{assists}</td></tr>"
        )
    card = (
        "<div class='card'><table><thead><tr>"
        "<th class='num'>#</th><th>Player</th><th>Team</th><th>League</th>"
        "<th class='num'>Min</th><th class='num'>npxG/90</th><th class='num'>xA/90</th>"
        "<th class='num'>npxG+xA/90</th><th class='num'>npG+A</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )
    about = (
        f"<p><strong>What it shows.</strong> The {limit} most dangerous attackers across "
        "all five leagues, ranked by npxG+xA per 90 — the value of the shots a player "
        "takes plus the chances they create, penalties excluded, per full match played. "
        f"Only players with {min_minutes}+ minutes (roughly 15 full matches) qualify, so "
        "small-sample super-subs don't flood the list. npG+A is the raw non-penalty "
        "goals plus assists actually banked.</p>"
        "<p><strong>How to read it.</strong> This is a threat ranking, not a talent "
        "ranking: it measures the danger a player generated in <em>their own</em> "
        "league (see the note at the top of this tab). It also leans attacking by "
        "construction — deep playmakers and defenders live in the xGBuildup column "
        "of the player explorer, not here.</p>"
    )
    return block("Most dangerous attackers in Europe", card, about)


def europe_justice_table(db, limit=20):
    rows = db.execute(
        f"""SELECT team, league, COUNT(*), SUM(pts), SUM(xpts), SUM(npxgd)
           FROM understat_team_matches
           WHERE league IN ({",".join("?" * len(UNDERSTAT_LEAGUES))})
           GROUP BY team, league
           ORDER BY SUM(xpts) * 1.0 / COUNT(*) DESC, team LIMIT ?""",
        (*UNDERSTAT_LEAGUES, limit),
    ).fetchall()
    if not rows:
        return ""
    body = ""
    for rank, (team, lg, games, pts, xpts, npxgd) in enumerate(rows, 1):
        body += (
            f"<tr><td class='num'>{rank}</td>"
            f"<td>{_analytics_label(team, lg)}</td>"
            f"<td class='dim'>{escape(lg)}</td><td class='num'>{games}</td>"
            f"<td class='num score'>{xpts / games:.2f}</td>"
            f"<td class='num'>{pts / games:.2f}</td>"
            f"<td class='num'>{fmt_delta_html((pts - xpts) / games, 2)}</td>"
            f"<td class='num'>{fmt_delta_html(npxgd / games, 2)}</td></tr>"
        )
    card = (
        "<div class='card'><table><thead><tr>"
        "<th class='num'>#</th><th>Team</th><th>League</th><th class='num'>P</th>"
        "<th class='num'>xPts/m</th><th class='num'>Pts/m</th>"
        "<th class='num'>Pts−xPts/m</th><th class='num'>npxGD/m</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )
    about = (
        f"<p><strong>What it shows.</strong> The continental justice table: the {limit} "
        "strongest teams in Europe by <em>expected</em> points per match, i.e. what each "
        "team's chances deserved. Everything is per match because the Bundesliga and "
        "Ligue 1 play 34 games while the other three play 38 — season totals would "
        "flatter the longer leagues.</p>"
        "<p><strong>How to read it.</strong> npxGD/m (non-penalty xG difference per "
        "match) is the best single strength number; Pts−xPts/m above zero means the "
        "team banked more than its chances deserved. And once more: each team earned "
        "these numbers against its own league's opposition, so this ranks domestic "
        "dominance, not head-to-head strength.</p>"
    )
    # says "opens" rather than "loads below": this table is on another tab,
    # so the click moves the page to Team analytics in the club's own league
    hint = ("<p class='meta team-hint' hidden>Click a club to open it in "
            "Team analytics.</p>")
    return block("Continental justice table — xPts per match",
                 hint + card, about)


def europe_panel(db):
    caveat = (
        "<div class='caveat'>"
        "<p><strong>Read this first.</strong> This tab pours five very different "
        "leagues into one pot — and that comparison is fundamentally flawed. The xG "
        "model prices a chance the same everywhere, but the leagues differ wildly in "
        "pace, defensive quality, tactical style and squad depth: 0.8 npxG+xA per 90 "
        "against Ligue 1 defences is not the same achievement as the same number in "
        "the Premier League, and no cross-league adjustment is applied here.</p>"
        "<p>Treat these boards as a fun conversation starter, not a verdict.</p>"
        "</div>"
    )
    return (
        f"<h2>Best of Europe <span class='dim'>({season_label(db)}, Understat)</span></h2>"
        "<p class='meta'>Continental leaderboards: the five leagues' players and teams "
        "in one view.</p>"
        + caveat + europe_attackers_table(db) + europe_justice_table(db)
    )


# -------------------------------------------------------------- player tab

def finishing_rows(db, league, order, limit=8, min_minutes=900):
    return db.execute(
        f"""SELECT player_name, team, minutes, shots, goals, xg, goals - xg AS diff
            FROM understat_players WHERE league = ? AND minutes >= ?
            ORDER BY diff {order}, player_name LIMIT ?""",
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
           ORDER BY xa DESC, player_name LIMIT ?""",
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
                  assists, xa, shots, key_passes, npg, npxg, xg_chain, xg_buildup,
                  player_id
           FROM understat_players WHERE league = ?
           ORDER BY xg DESC, player_name, team""",
        (league,),
    ).fetchall()
    return [
        {
            # Understat stores some names entity-encoded ("M&#039;Bala Nzola")
            "name": unescape(r[0]), "team": unescape(r[1]), "pos": r[2] or "", "games": r[3],
            "id": r[15],
            "min": r[4], "goals": r[5], "xg": round(r[6], 2),
            "assists": r[7], "xa": round(r[8], 2), "shots": r[9], "kp": r[10],
            "npg": r[11], "npxg": round(r[12], 2),
            "chain": round(r[13], 2), "buildup": round(r[14], 2),
            "gdiff": round(r[5] - r[6], 2), "adiff": round(r[7] - r[8], 2),
        }
        for r in rows
    ]


def pack_by_league(by_lg, intern=()):
    """A per-league list of uniform dicts, re-encoded as arrays plus one key
    list, with the named fields interned into a shared table.

    Every row of these payloads carries the same keys, so shipping them as
    objects spells each key name out once per row -- fifteen of them per
    player, across sixteen hundred players -- and repeats a club name once per
    squad member. The client rehydrates this straight back into the same
    objects, so nothing downstream changes; what changes is the wire.
    """
    sample = next((v for v in by_lg.values() if v), None)
    if sample is None:
        return {"k": [], "c": [], "i": [], "d": {lg: [] for lg in by_lg}}
    keys = list(sample[0].keys())
    idx = [keys.index(k) for k in intern if k in keys]
    table = {}

    def cell(j, value):
        if j not in idx:
            return value
        return table.setdefault(value, len(table))

    # the rows are built before the string table is read out of the closure
    data = {lg: [[cell(j, r[k]) for j, k in enumerate(keys)] for r in rs]
            for lg, rs in by_lg.items()}
    return {"k": keys, "c": list(table), "i": idx, "d": data}


# the client half of pack_by_league, emitted once and shared by every payload
# that uses it. Assigned onto window rather than declared, so the second script
# tag reuses it instead of redeclaring a const in the same global scope.
UNPACK_JS = (
    "window.UNPACK=window.UNPACK||function(b){var o={},k=b.k,c=b.c||[],"
    "s=new Set(b.i||[]);Object.keys(b.d).forEach(function(lg){"
    "o[lg]=b.d[lg].map(function(r){var x={};for(var j=0;j<k.length;j++)"
    "x[k[j]]=s.has(j)?c[r[j]]:r[j];return x;});});return o;};"
)


def load_player_careers(db, players_by_lg):
    """Every stored season of every player the explorer can open, as one blob.

    Not per league, unlike the other payloads: a career crosses leagues, and
    Salah's Roma seasons belong on his card whichever league you opened it
    from. Keyed by Understat's player_id rather than by name -- names collide,
    and the feed stores some of them entity-encoded.

    Capped at the newest season the scoped view can see, like the club history
    strip, so an archive page shows a career as it stood that year and not a
    line about a club the player had yet to join.

    Seasons and club names are interned into shared tables. Spelling them out
    per row costs about 180 KB on a page that is already large, and the same
    two hundred club names repeat seven thousand times.
    """
    ids = {p["id"] for ps in players_by_lg.values() for p in ps if p.get("id")}
    if not ids:
        return {}
    cap = db.execute("SELECT MAX(season) FROM understat_players").fetchone()[0]
    rows = db.execute(
        """SELECT player_id, season, league, team, games, minutes, goals, xg,
                  assists, xa
           FROM main.understat_players WHERE season <= ?""",
        (cap,),
    ).fetchall()
    careers = {}
    for r in rows:
        if r[0] in ids:
            careers.setdefault(r[0], []).append(r)

    # the season table is filled in order before anything else is interned:
    # the client walks it by index to find the years a player is missing from,
    # so an index that is not chronological silently mislabels every gap
    seasons = {s: i for i, (s,) in enumerate(db.execute(
        "SELECT DISTINCT season FROM main.understat_players WHERE season <= ? "
        "ORDER BY season", (cap,)))}
    leagues, clubs = {}, {}

    def intern(table, value):
        return table.setdefault(value, len(table))

    out = {}
    for pid, rs in careers.items():
        # one season is the row the card already shows; a career needs two
        if len({r[1] for r in rs}) < 2:
            continue
        # within a season, clubs are ordered by minutes: a mid-season move
        # leaves two rows and the feed carries no dates to order them by
        rs.sort(key=lambda r: (r[1], -r[5]))
        out[pid] = [
            [intern(seasons, r[1]), intern(leagues, r[2]), intern(clubs, unescape(r[3])),
             r[4], r[5], r[6], round(r[7], 1), r[8], round(r[9], 1)]
            for r in rs
        ]
    if not out:
        return {}
    return {"s": [_season_label("Premier League", s) for s in seasons],
            "l": list(leagues), "c": list(clubs), "p": out}


def player_explorer(players_by_lg, careers=None):
    if not any(players_by_lg.values()):
        return ""
    # team filter and datalist options are (re)built client-side per league.
    # Packed rather than sent as objects: this is the heaviest payload on the
    # page by some way, and every player carries the same fifteen key names
    # and one of two hundred club names
    payload = json.dumps(
        pack_by_league(players_by_lg, intern=("team", "pos")),
        ensure_ascii=False, separators=(",", ":"),
    ).replace("</", "<\\/")
    career_payload = json.dumps(
        careers or {}, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")

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
        f"<script>{UNPACK_JS}\nconst PLAYERS_BY_LG = UNPACK({payload});\n"
        f"const CAREERS = {career_payload};</script>"
    )
    total = sum(len(v) for v in players_by_lg.values())
    about = (
        f"<p><strong>What it shows.</strong> Every player Understat tracks in the "
        f"selected league this season ({total} across the big five). The table starts "
        "with the top 25 by the current sort — use "
        "the buttons under it to load more. Search by name or club, filter by position "
        "and minutes, and click any column header to sort (click again to flip direction). "
        "<strong>Click a row</strong> to open that player's profile card, with season "
        "totals, percentile bars against players of the same position, and the whole "
        "career Understat has stored underneath.</p>"
        "<p><strong>The career strip.</strong> One row per season on the profile card, "
        "back to 2014/15, with the club, the minutes, goals against xG and assists "
        "against xA \u2014 so a season reads against the rest of the player's career "
        "rather than on its own. Moves draw themselves: a club in another league is "
        "labelled and dimmed, and a mid-season transfer leaves two rows under one "
        "season, ordered by minutes because the feed carries no dates to order them "
        "by. A season with no row is one Understat did not cover \u2014 it tracks the "
        "big five only, so a gap means a spell outside them (a second division, a "
        "league elsewhere), not a year out of football. The bar is goals minus xG, "
        "which over a career is the difference between a finisher having one hot "
        "season and being a good finisher.</p>"
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
    # datalist options are built client-side over every league ("Name — Team · League"
    # values), so the comparison works across leagues regardless of the switcher
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
        "to see their profiles side by side. The search covers all five leagues, so "
        "cross-league match-ups work too.</p></div>"
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
        "players of the same position <em>in their own league</em> with 450+ minutes, "
        "so a defender isn't drowned by striker numbers; the table below gives the "
        "exact per-90 rates.</p>"
        "<p><strong>How to read it.</strong> The bigger the shape, the more complete the "
        "attacking contribution — but shape <em>profile</em> matters more than area: a "
        "pure finisher spikes toward npxG and shots, a creator toward xA and key passes, "
        "a deep engine toward xGBuildup. Comparing a striker with a full-back is fair "
        "here because each is measured against their own position group — and since the "
        "search spans all five leagues, cross-league match-ups work the same way: each "
        "player is ranked against their own league's peers, so the radar answers "
        "“who dominates their context more”, not who would outscore whom in the same "
        "league.</p>"
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
    # FotMob-backed leagues have no PPDA/deep completions: those stay None
    # and the comparison UI drops the corresponding axes client-side
    per_match = lambda v, n, dec: round(v / n, dec) if v is not None else None
    return [
        {
            "team": unescape(r[0]), "mp": r[1], "pts": r[2], "xpts": round(r[3], 1),
            "npxg": round(r[4] / r[1], 2), "npxga": round(r[5] / r[1], 2),
            "ppda": round(r[6], 1) if r[6] is not None else None,
            "deep": per_match(r[7], r[1], 1), "deep_allowed": per_match(r[8], r[1], 1),
            "gpm": round(r[9] / r[1], 2), "cpm": round(r[10] / r[1], 2),
            "gdiff": round(r[9] - r[11], 1), "gadiff": round(r[12] - r[10], 1),
            "ptsdiff": round(r[2] - r[3], 1),
        }
        for r in rows
    ]


def load_club_history(db, league, names):
    """One row per season for each club on this page, read deliberately across
    seasons.

    Every other query in this file is season-scoped -- that is what the temp
    views are for -- so this one reads main. on purpose. A club's history is
    the one question a single-season page cannot answer out of its own rows,
    and twelve seasons of it were already stored and unread.

    The cap is the newest season the *scoped* view can see, not MAX(season) in
    the table. On the live dashboard those are the same thing; on an archive
    page they are not, and reading past the cap would print a club's future on
    a page dated years earlier. It also keeps the archives deterministic: a
    finished season's history cannot change, so those files still only churn
    when the code does.
    """
    cap = db.execute(
        "SELECT MAX(season) FROM understat_team_matches WHERE league = ?", (league,)
    ).fetchone()[0]
    if cap is None or not names:
        return {}
    table = ("main.understat_team_matches" if league in UNDERSTAT_LEAGUES
             else "main.fotmob_team_matches")
    if not fotmob_available(db) and table.endswith("fotmob_team_matches"):
        return {}
    rows = db.execute(
        f"""SELECT season, team, COUNT(*), SUM(pts), SUM(xpts),
                   SUM(scored), SUM(missed), SUM(xg), SUM(xga),
                   SUM(pts = 3), SUM(pts = 1), SUM(pts = 0)
            FROM {table} WHERE league = ? AND season <= ?
            GROUP BY season, team""",
        (league, cap),
    ).fetchall()
    if not rows:
        return {}
    # every club that played, not just the selectable ones: a position is only
    # right if it is computed against the whole division
    by_season = {}
    for r in rows:
        by_season.setdefault(r[0], []).append(r)
    wanted = set(names)
    clubs, seasons = {}, []
    for season in sorted(by_season):
        seasons.append(_season_label(league, season))
        table_rows = sorted(
            by_season[season],
            key=lambda r: (-r[3], -(r[5] - r[6]), -r[5]),   # pts, GD, GF
        )
        for pos, r in enumerate(table_rows, 1):
            team = unescape(r[1])
            if team not in wanted:
                continue
            clubs.setdefault(team, []).append([
                _season_label(league, season), pos, len(table_rows), r[2],
                r[9], r[10], r[11], r[5] - r[6], round(r[7] - r[8], 1),
                r[3], round(r[4], 1),
            ])
    return {"seasons": seasons, "clubs": clubs}


def load_squads(db, league, names):
    """Squads for the leagues the player explorer does not cover.

    The big five need nothing here: their players are already on the page in
    PLAYERS_BY_LG, carrying the club each one plays for, so the club card
    filters that list rather than being sent a second copy of it. This exists
    for a FotMob-backed league, whose players never reach the explorer.

    Those two feeds also disagree about club names -- FotMob's player table
    says "Hammarby IF" where its match table says "Hammarby" -- so the same
    normaliser the predictions use bridges them. It maps all sixteen
    Allsvenskan clubs with no new aliases.
    """
    if league in UNDERSTAT_LEAGUES or not fotmob_available(db) or not names:
        return {}
    rows = db.execute(
        """SELECT team, player_name, matches, minutes, goals, xg, assists, xa,
                  player_id, shots, shots_on_target, chances_created, xgot
           FROM fotmob_players WHERE league = ? AND minutes > 0
           ORDER BY minutes DESC""",
        (league,),
    ).fetchall()
    if not rows:
        return {}
    bridge = _predict_mapping(sorted({unescape(r[0]) for r in rows}), list(names))
    squads = {}
    for r in rows:
        club = bridge.get(unescape(r[0]))
        if not club:
            continue
        # the first nine columns are the row shape the client derives for the
        # big five, so the club card renders both the same way. Position is
        # blank because FotMob's player feed has no column for it. The four
        # after the id are what a reduced profile card can be built from:
        # xGChain, xGBuildup and non-penalty xG are not published here, which
        # is why this league has curated boards rather than the full explorer
        squads.setdefault(club, []).append(
            [unescape(r[1]), "", r[2], r[3], r[4], round(r[5], 2),
             r[6], round(r[7], 2), r[8], r[9], r[10], r[11],
             round(r[12], 2) if r[12] is not None else 0]
        )
    return squads


def load_team_matches(db, league):
    # per-team chronological match lists power the head-to-head deep dive:
    # each entry is [date, home_away, goals_for, goals_against, xg, xga, npxgd, pts]
    rows = db.execute(
        """SELECT team, match_date, home_away, scored, missed, xg, xga, npxgd, pts
           FROM understat_team_matches WHERE league = ? ORDER BY team, match_date""",
        (league,),
    ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(unescape(r[0]), []).append(
            [r[1][:10], r[2], r[3], r[4],
             round(r[5], 2), round(r[6], 2), round(r[7], 2), r[8]]
        )
    return out


# ---------------------------------------------------- fixture explorer data

FIXTURES_SHOWN = PREDICT_SHOWN  # same slate as the Predictions block
RESULTS_SHOWN = 10              # matches the Recent results table lists
FIXTURE_FORM = 6                # recent matches shown per side
FIXTURE_H2H = 8                 # past meetings shown
FIXTURE_PLAYERS = 5             # top scorers/creators per side


def _resolved_matches(db, league):
    """Every stored match for a league as (season, date, home, away, hg, ag,
    h_xg, a_xg), newest last — across all seasons, not just the current one.

    Understat's team feed has no opponent column: it stores one row per team
    per match, and the two halves of a fixture have to be paired back up.
    Kickoff timestamp alone is not enough — two clubs playing different
    opponents at the same time pair up spuriously, which handed Arsenal and
    Chelsea three meetings in a two-fixture season. Requiring the scoreline
    and both xG figures to mirror as well pins it down exactly: across the
    21,598 stored home rows this resolves 21,597 of them, each to exactly
    one opponent and none to two. The single miss is the Ligue 1 fixture
    whose opponent Understat is still serving without a name (see
    fetch_understat.py) — it reappears by itself once they fix it upstream.

    Allsvenskan skips all of that: its FotMob-sourced table already names
    the opponent.
    """
    if league in UNDERSTAT_LEAGUES:
        rows = db.execute(
            """SELECT h.season, h.match_date, h.team, a.team,
                      h.scored, h.missed, h.xg, h.xga
               FROM main.understat_team_matches h
               JOIN main.understat_team_matches a
                 ON h.league = a.league AND h.season = a.season
                AND h.match_date = a.match_date
                AND h.scored = a.missed AND h.missed = a.scored
                AND ABS(h.xg - a.xga) < 0.0001 AND ABS(h.xga - a.xg) < 0.0001
               WHERE h.league = ? AND h.home_away = 'h' AND a.home_away = 'a'
                 AND h.team <> a.team
               ORDER BY h.match_date""",
            (league,),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT season, match_date, team, opponent, scored, missed, xg, xga
               FROM main.fotmob_team_matches
               WHERE league = ? AND home_away = 'h'
               ORDER BY match_date""",
            (league,),
        ).fetchall()
    return [
        (r[0], r[1][:10], unescape(r[2]), unescape(r[3]), r[4], r[5],
         round(r[6], 2), round(r[7], 2))
        for r in rows
    ]


def _forecasts_for(db, league, names):
    """Understat's post-match forecast, keyed by (date, home, away) using the
    caller's own club names.

    The forecast is a rerun of the match from the chances both sides created,
    so it exists only once a match has been played, and only in the five
    leagues Understat covers -- Allsvenskan's feed has no equivalent and
    simply gets nothing. Each entry carries the scoreline Understat has on
    file so the caller can refuse a row when the two feeds turn out to be
    describing different 90 minutes.

    Understat rounds each probability to four places, so a set can sum to
    0.99; they are renormalised here rather than left to quietly bias every
    number computed from them.
    """
    if league not in UNDERSTAT_LEAGUES or not names:
        return {}
    # a database from before the table existed must degrade to "no forecast",
    # not crash the whole build
    if not db.execute(
        "SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?",
        ("understat_fixtures",),
    ).fetchone():
        return {}
    rows = db.execute(
        """SELECT kickoff, home, away, home_goals, away_goals,
                  fc_home, fc_draw, fc_away
           FROM main.understat_fixtures
           WHERE league = ? AND fc_home IS NOT NULL""",
        (league,),
    ).fetchall()
    if not rows:
        return {}
    us_names = sorted({unescape(r[1]) for r in rows} | {unescape(r[2]) for r in rows})
    back = {us: own for own, us
            in _predict_mapping(sorted(set(names)), us_names).items() if us}
    out = {}
    for kickoff, home, away, hg, ag, w, d, l in rows:
        h, a = back.get(unescape(home)), back.get(unescape(away))
        total = w + d + l
        if not (h and a) or total <= 0:
            continue
        out[((kickoff or "")[:10], h, a)] = ([w / total, d / total, l / total], hg, ag)
    return out


def _season_label(league, season):
    """'2025' -> '2025/26' for the big five, left alone for calendar-year
    leagues (Allsvenskan plays 2026 inside 2026)."""
    if season is None:
        return None
    return f"{season}/{int(season) % 100 + 1}" if league in UNDERSTAT_LEAGUES else str(season)


PLAYERS_MIN_MINUTES = 270   # ~3 full matches: below this a season's scoring
                            # leaders are whoever happened to score first


def _fixture_players(db, league, fx_names):
    """Top few attacking contributors per club, each with the season they are
    from, keyed by *fixture* name.

    The player tables need their own name bridge rather than borrowing the
    match tables': all three feeds disagree, and they disagree differently.
    Understat calls Mainz "Mainz 05" in both, but FotMob's player feed uses
    full club names ("IF Elfsborg", "Hammarby IF") where its own match feed
    uses short ones. Matching on the source name would silently drop every
    club whose two names differ — which is most of Allsvenskan.

    Every club is resolved independently to the newest season in which it has
    a meaningful amount of football played, because in August it does not have
    one yet. Showing an empty box for every big-five club through the opening
    weeks would make the section dead weight at exactly the moment someone is
    looking at an opening-weekend fixture; last season's leading attackers are
    genuinely informative there, as long as the box says that is what they are.
    """
    table = "understat_players" if league in UNDERSTAT_LEAGUES else "fotmob_players"
    if table == "fotmob_players" and not fotmob_available(db):
        return {}
    rows = db.execute(
        f"""SELECT season, team, player_name, minutes, goals, xg, assists, xa
            FROM main.{table} WHERE league = ? AND minutes > 0
            ORDER BY season DESC, team, (goals + assists) DESC, (xg + xa) DESC""",
        (league,),
    ).fetchall()
    squad_names = {unescape(r[0]) for r in db.execute(
        f"SELECT DISTINCT team FROM main.{table} WHERE league = ?", (league,))}
    want = {hist: fx for fx, hist
            in _predict_mapping(fx_names, list(squad_names)).items() if hist}
    by_season = {}
    for season, team, name, minutes, goals, xg, assists, xa in rows:
        fx = want.get(unescape(team))
        if fx is None:
            continue
        by_season.setdefault(fx, {}).setdefault(season, []).append(
            [unescape(name), minutes, goals, round(xg or 0, 1),
             assists, round(xa or 0, 1)]
        )
    out = {}
    for team, seasons in by_season.items():
        ordered = sorted(seasons, reverse=True)
        pick = next(
            (s for s in ordered
             if sum(p[1] for p in seasons[s]) >= PLAYERS_MIN_MINUTES * FIXTURE_PLAYERS),
            ordered[0],
        )
        out[team] = {"season": _season_label(league, pick),
                     "rows": seasons[pick][:FIXTURE_PLAYERS]}
    return out


def _club_history(played, name):
    """One club's matches from a _resolved_matches list, newest first, as
    (season, [date, home/away, opponent, scored, conceded, xG, xGA]).

    Always from that club's own point of view, so an away row reads the same
    way a home row does and nothing downstream has to remember which side of
    the fixture it is looking at.
    """
    rows = []
    for season, mdate, home, away, hg, ag, hxg, axg in played:
        if home == name:
            rows.append((season, [mdate, "h", away, hg, ag, hxg, axg]))
        elif away == name:
            rows.append((season, [mdate, "a", home, ag, hg, axg, hxg]))
    rows.reverse()
    return rows


def load_team_form(db, league, limit=FIXTURE_FORM):
    """Recent form for every club Team analytics covers, keyed by the name
    that tab knows it by.

    The fixture explorer already builds this, but keyed by TheSportsDB's
    names and only for the clubs on its slate -- a club with no upcoming
    fixture and no recent result would be missing exactly when its profile
    is the only place left to read about it. Keying off load_teams instead
    covers the tab completely and needs no name bridge, since both come from
    the same feed.
    """
    names = [t["team"] for t in load_teams(db, league)]
    if not names:
        return {}
    played = _resolved_matches(db, league)
    out = {}
    for name in names:
        rows = [r for _, r in _club_history(played, name)[:limit]]
        if rows:
            out[name] = rows
    return out


def load_fixture_data(db, league):
    """Everything the fixture explorer needs for one league, as plain JSON.

    Shaped to avoid repeating a club's form once per fixture it appears in:
    `teams` is keyed by club and `fixtures` just names them, so adding
    fixtures costs a line each rather than a full record.
    """
    fixtures = db.execute(
        """SELECT event_id, match_date, match_time, round, home_team, away_team
           FROM matches WHERE league = ? AND home_score IS NULL AND match_date >= ?
           ORDER BY match_date, event_id LIMIT ?""",
        (league, date.today().isoformat(), FIXTURES_SHOWN),
    ).fetchall()
    results = db.execute(
        """SELECT event_id, match_date, match_time, round, home_team, away_team,
                  home_score, away_score
           FROM matches WHERE league = ? AND home_score IS NOT NULL
           ORDER BY match_date DESC, event_id LIMIT ?""",
        (league, RESULTS_SHOWN),
    ).fetchall()
    if not fixtures and not results:
        return None

    # fixture names come from TheSportsDB, the xG history from Understat or
    # FotMob; _predict_mapping is the same bridge the predictions use
    played = _resolved_matches(db, league)
    hist_names = {m[2] for m in played} | {m[3] for m in played}
    fx_names = sorted(
        {n for _, _, _, _, h, a in fixtures for n in (h, a)}
        | {n for _, _, _, _, h, a, _, _ in results for n in (h, a)}
    )
    mapping = _predict_mapping(fx_names, list(hist_names))

    strengths, mu, home_adv, lg_deep = _team_strengths(db, league)
    s_mapping = _predict_mapping(fx_names, list(strengths)) if strengths else {}

    # per-club: recent form (newest first) and the venue split that actually
    # applies to it in this fixture — a home side's home record, not its overall
    form, venue = {}, {}
    for fx_name in fx_names:
        hist = mapping.get(fx_name)
        if hist is None:
            continue
        rows = _club_history(played, hist)
        form[fx_name] = [r for _, r in rows[:FIXTURE_FORM]]
        # venue records are this season only; form deliberately is not, so a
        # club's last six carry over the summer instead of showing nothing.
        # Season labels differ per league (Allsvenskan is a calendar year,
        # the big five span two), so the club's own newest label is the anchor
        latest = rows[0][0] if rows else None
        split = {"h": [0, 0, 0, 0.0, 0.0], "a": [0, 0, 0, 0.0, 0.0]}
        for season, (mdate, ha, opp, gf, ga, xg, xga) in rows:
            if season != latest:
                continue
            rec = split[ha]
            rec[0] += 1
            rec[1] += gf
            rec[2] += ga
            rec[3] += xg
            rec[4] += xga
        # the venue record answers "how do they go at this ground", the
        # combined one "how good are they, full stop" — a side that is
        # excellent at home and ordinary overall is the interesting case, and
        # showing only one half of it hides that
        both = [split["h"][i] + split["a"][i] for i in range(5)]
        venue[fx_name] = {
            "season": _season_label(league, latest),
            "h": [split["h"][0], split["h"][1], split["h"][2],
                  round(split["h"][3], 1), round(split["h"][4], 1)],
            "a": [split["a"][0], split["a"][1], split["a"][2],
                  round(split["a"][3], 1), round(split["a"][4], 1)],
            "all": [both[0], both[1], both[2], round(both[3], 1), round(both[4], 1)],
        }

    players = _fixture_players(db, league, fx_names)

    out_fixtures, h2h = [], {}

    def add_h2h(rec, home, away, before=None):
        """Attach past meetings between these two, newest first.

        `before` drops meetings from that date on, so a match report shows
        the history the two sides brought into it rather than including the
        match being reported.
        """
        hh, ha_ = mapping.get(home), mapping.get(away)
        if not (hh and ha_):
            return
        meetings = [
            [m[1], m[2] == hh, m[4], m[5], m[6], m[7]]
            for m in played
            if {m[2], m[3]} == {hh, ha_} and (before is None or m[1] < before)
        ]
        if meetings:
            key = f"{home}|{away}|{rec['id']}"
            h2h[key] = list(reversed(meetings))[:FIXTURE_H2H]
            rec["h2h"] = key

    for event_id, mdate, mtime, rnd, home, away in fixtures:
        rec = {
            "id": event_id, "date": (mdate or "")[:10], "time": (mtime or "")[:5],
            "round": rnd, "home": home, "away": away,
        }
        sh, sa = s_mapping.get(home), s_mapping.get(away)
        if sh and sa:
            lam_h, lam_a, n_min = _fixture_lambdas(
                strengths, mu, home_adv, lg_deep, sh, sa)
            probs = _outcome_probs(lam_h, lam_a)
            rec["p"] = [round(p, 4) for p in probs]
            # the displayed integers are rounded here rather than in the
            # browser: Python rounds a half to even and JavaScript rounds it
            # up, so a 18.5% draw showed as 18% on the League tab and 19%
            # here — the same model appearing to contradict itself
            rec["pct"] = [int(f"{p * 100:.0f}") for p in probs]
            rec["lam"] = [round(lam_h, 2), round(lam_a, 2)]
            rec["n"] = n_min
        else:
            # a promoted club with no top-flight xG history: say so rather
            # than showing an invented probability
            rec["nohist"] = [n for n in (home, away) if not s_mapping.get(n)]
        add_h2h(rec, home, away)
        out_fixtures.append(rec)

    # ---- match reports for matches already played -----------------------
    # the actual xG of a given match, keyed the way the results table names
    # the clubs, so a report can say what the chances were worth on the day
    xg_by_match = {}
    for _, mdate, h, a, hg, ag, hxg, axg in played:
        xg_by_match[(mdate, h, a)] = (hg, ag, hxg, axg)
    # Understat's rerun of the same match, keyed the same way
    forecasts = _forecasts_for(db, league, fx_names)

    logged = prediction_log.load()
    out_results = []
    for event_id, mdate, mtime, rnd, home, away, hg, ag in results:
        day = (mdate or "")[:10]
        rec = {
            "id": event_id, "kind": "r", "date": day, "time": (mtime or "")[:5],
            "round": rnd, "home": home, "away": away, "hg": hg, "ag": ag,
        }
        hit = xg_by_match.get((day, mapping.get(home), mapping.get(away)))
        if hit:
            # the score is taken from the xG feed's own row for the match, not
            # from TheSportsDB, so the goals and the xG beside them always
            # describe the same 90 minutes; a disagreement means the two feeds
            # are describing different matches and the xG is left off
            if hit[0] == hg and hit[1] == ag:
                rec["hxg"], rec["axg"] = hit[2], hit[3]
        # what the chances deserved: the same scoreline check applies, since a
        # forecast attached to the wrong match would read as a confident lie
        fc = forecasts.get((day, home, away))
        if fc and fc[1] == hg and fc[2] == ag:
            rec["fc"] = [round(p, 4) for p in fc[0]]
            rec["fcpct"] = [int(f"{p * 100:.0f}") for p in fc[0]]
        # what the model said in advance, if this fixture was ever published
        row = logged.get(str(event_id))
        if row:
            try:
                probs = prediction_log.probabilities(row)
                rec["p"] = [round(p, 4) for p in probs]
                rec["pct"] = [int(f"{p * 100:.0f}") for p in probs]
                rec["lam"] = [round(float(row["lam_home"]), 2),
                              round(float(row["lam_away"]), 2)]
                rec["first"] = row["first_seen"]
                rec["last"] = row["last_seen"]
            except (KeyError, TypeError, ValueError):
                pass
        add_h2h(rec, home, away, before=day)
        out_results.append(rec)

    return {"fixtures": out_fixtures, "results": out_results, "form": form,
            "venue": venue, "players": players, "h2h": h2h}


def team_compare(teams_by_lg, tm_by_lg, form_by_lg, hist_by_lg=None,
                 squads_by_lg=None):
    if not any(teams_by_lg.values()):
        return ""
    # select options are (re)built client-side per league
    selects = "".join(
        f"<select id='tc-{i}'><option value=''>Team {i}…</option></select>"
        for i in (1, 2, 3)
    )
    # club names are unique per row here, so there is nothing to intern --
    # the saving is the fourteen repeated key names
    payload = json.dumps(
        pack_by_league(teams_by_lg), ensure_ascii=False, separators=(",", ":"),
    ).replace("</", "<\\/")
    tm_payload = json.dumps(
        tm_by_lg, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    form_payload = json.dumps(
        form_by_lg, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    hist_payload = json.dumps(
        hist_by_lg or {}, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    squad_payload = json.dumps(
        squads_by_lg or {}, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    # empty on the archive pages, where cross-season form has no business being
    has_form = any(form_by_lg.values())
    body = (
        f"<div class='controls'>{selects}"
        "<button id='tc-clear' type='button'>Clear</button></div>"
        "<div class='chart-card' id='tc-empty'><p class='dim' style='margin:4px 2px'>"
        "Pick a team above for its style profile" + (" and recent form" if has_form else "")
        + ", or two or three to "
        "see them side by side. Picking exactly <em>two</em> unlocks a head-to-head deep dive: this "
        "season's meetings, a tale-of-the-tape bar duel, recent form, home/away splits "
        "and overlaid form curves.</p></div>"
        "<div class='chart-card' id='tc-card' hidden></div>"
        f"<script>{UNPACK_JS}\nconst TEAMS_BY_LG = UNPACK({payload});\n"
        f"const TM_BY_LG = {tm_payload};\n"
        f"const FORM_BY_LG = {form_payload};\n"
        f"const HIST_BY_LG = {hist_payload};\n"
        f"const SQUADS_BY_LG = {squad_payload};</script>"
    )
    about = (
        "<p><strong>What it shows.</strong> One to three teams overlaid on a radar of six "
        "style dimensions, each expressed as the team's <em>percentile</em> among the "
        "sides in that league. A single team is a profile rather than a comparison — "
        "that is where a click on a club's name over on the League tab lands"
        + (", and it carries the club's last six matches underneath, because the radar "
           "says what kind of side this is and only the results say how it is going"
           if has_form else "")
        + ". <strong>Attack</strong> is non-penalty xG "
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
        "<p><strong>Season by season.</strong> Under a single club, one row per season "
        "as far back as the data goes \u2014 where they finished, the record, goal "
        "difference, xG difference, and points against expected points. The bar is the "
        "gap between those last two: right and green means the club banked more than its "
        "chances deserved, left and red means it banked fewer. It is the luck quadrant "
        "from the Insights tab asked of a decade instead of a season, and it is what "
        "says whether a good year was a step up or a hot streak. A season spent in a "
        "lower division shows as a gap rather than being skipped, so the relegations "
        "are visible too. One caveat: the position is computed from the stored results, "
        "so it does not know about points deductions \u2014 Everton and Nottingham "
        "Forest in 2023/24, and Juventus in 2022/23, finished lower than this table "
        "puts them.</p>"
        "<p><strong>The squad.</strong> Under a single club, everyone who has "
        "played a minute for it in the season the page is about, most minutes "
        "first, with goals against xG and assists against xA. It lists players "
        "<em>used</em>, not a registered squad \u2014 neither feed carries a "
        "player until they have appeared \u2014 so a club one matchday into a "
        "season shows the eleven and its substitutes and grows to the "
        "mid-twenties by May. In the big five a "
        "name opens that player's profile card and career underneath; "
        "Allsvenskan's squads come from FotMob, which publishes no positions "
        "and nothing the player explorer is built on, so those names are listed "
        "rather than linked. It costs the page nothing in the big five \u2014 "
        "those players are already loaded for the Players tab, so the squad is "
        "a filter over them rather than a second copy.</p>"
        "<p><strong>Head-to-head mode.</strong> With exactly two teams picked, the card "
        "goes deeper: a tale-of-the-tape strip where each bar is split by the two sides' "
        "league-percentile share (the longer half leads, the number in brackets is the "
        "league rank as a percentile ordinal), this season's actual meetings between the "
        "clubs with the score <em>and</em> each side's xG that day, the last five "
        "results, points and non-penalty xG difference split by home/away, and both "
        "teams' rolling 6-match npxGD form curves overlaid on one chart — so you can "
        "see not just who is better on the season, but who is better <em>right "
        "now</em>, and what happened when they actually met.</p>"
    )
    return block("Team comparison", body, about)


def fixtures_panel(db, leagues):
    data = {lg: load_fixture_data(db, lg) for lg in leagues}
    data = {lg: d for lg, d in data.items() if d}
    if not data:
        return ""
    payload = json.dumps(
        data, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    body = (
        "<div class='controls'><select id='fx-pick'></select>"
        "<button id='fx-prev' type='button'>‹ Prev</button>"
        "<button id='fx-next' type='button'>Next ›</button></div>"
        "<div class='chart-card' id='fx-card'></div>"
        f"<script>const FIXTURES_BY_LG = {payload};</script>"
    )
    about = (
        "<p><strong>What it shows.</strong> One match at a time, in either tense. "
        "An <strong>upcoming fixture</strong> gets everything the site knows about "
        "the two clubs gathered in one place: the model's win/draw/win call, both "
        "sides' recent form in results <em>and</em> in chance quality, their past "
        "meetings, the venue split that actually applies to this match, and each "
        "squad's leading attackers.</p>"
        "<p><strong>A match already played</strong> gets a report instead: the "
        "score, what the chances were worth on the day, and — the part worth "
        "coming for — <em>what this site said about it beforehand</em>, taken from "
        "the call written down before kickoff and never edited since. It is marked "
        "called it or missed on the day's actual result. A match that was never in "
        "a predictions slate says so rather than inventing a retrospective opinion; "
        "that is usually a promoted club with no top-flight xG history at the time. "
        "Form and squad lists are deliberately left off a report — they would "
        "describe the clubs now, not as they were then.</p>"
        "<p>The head-to-head on a report stops <em>before</em> the match being "
        "reported, so it shows the history the two sides actually brought into it.</p>"
        "<p><strong>The verdict</strong> is the same Poisson model as the "
        "Predictions block on the League tab, reading from the same numbers — the "
        "two can never disagree about a match. Its caveats apply here in full: no "
        "transfers, no injuries, no suspensions, no new managers.</p>"
        "<p><strong>Form</strong> is the last six matches, newest first, with the "
        "score and both sides' xG that day. It deliberately reaches back across "
        "the summer rather than showing an empty strip in August — a club's last "
        "six competitive matches are still the best short-term evidence there is, "
        "even if some were played in May. <strong>By venue</strong> shows each club "
        "at the venue it will actually be at — the home side's home record against "
        "the visitors' away record — with its record across <em>all</em> matches "
        "underneath, because a side that is excellent at home and ordinary overall "
        "is exactly the case the venue row alone would hide. Both cover a single "
        "season, named in the heading. In "
        "August that is still last season for a club that has not kicked off yet, "
        "and the two sides of a fixture can even be anchored to different seasons; "
        "the heading says which rather than calling it all 'this season'. The same "
        "goes for <strong>leading attackers</strong>, labelled per club, which falls "
        "back to the last completed season until this one has enough minutes in it "
        "to mean anything.</p>"
        "<p><strong>Head-to-head</strong> reaches back as far as the xG data goes "
        "— up to twelve seasons — and gives each meeting's score alongside what "
        "the chances were worth. A club that keeps losing these while winning the "
        "xG is a different story from one that is simply outplayed. It is kept "
        "deliberately neutral: no win/loss colouring, because this is the two "
        "clubs' shared history rather than a run of form belonging to whichever "
        "side happens to be at home this time — the winner of each meeting is "
        "marked on its name, and the tally names both clubs. Meetings in other "
        "competitions are not here; this is league data only.</p>"
        "<p><strong>What's missing is marked.</strong> A promoted club has no "
        "top-flight xG history, so the model declines to predict its fixtures "
        "rather than inventing a number, and its form and head-to-head sections "
        "say so instead of showing an empty table.</p>"
    )
    return (
        "<h2>Matches</h2>"
        "<p class='meta'>Pick an upcoming fixture to see both clubs side by side, "
        "or a match already played to see the score, what the chances were worth, "
        "and what the model said about it beforehand.</p>"
        + block("Match explorer", body, about)
    )


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
    // push before the event, not after: the panels resettle while handling it
    // and replace the hash as they go, which would leave nothing for the push
    // to record and no history entry to come back to. Their replacements then
    // land inside the entry this just created, which is where they belong
    if (window.syncHash) window.syncHash(true);
    document.dispatchEvent(new CustomEvent('leaguechange'));
  }));
  // Back/Forward needs to change league without recording another step
  window.applyLeague = function (lg) {
    if (!lg || lg === window.CUR_LG) return false;
    let known = false;
    btns.forEach((b) => { if (b.dataset.lg === lg) known = true; });
    if (!known) return false;
    window.CUR_LG = lg;
    apply();
    document.dispatchEvent(new CustomEvent('leaguechange'));
    return true;
  };
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
    if (!PLAYERS.length) {
      // FotMob-backed leagues (Allsvenskan) have no Understat player feed;
      // their curated boards live further down the tab
      tbody.innerHTML = "<tr><td colspan='" + COLS.length + "' class='dim'>" +
        'No per-player Understat data for this league \\u2014 see the boards ' +
        'below (FotMob).</td></tr>';
    } else {
      tbody.innerHTML = shown.map((p) =>
        "<tr data-i='" + PLAYERS.indexOf(p) + "'>" + COLS.map((c, i) => {
          const cls = c.num ? 'num' : (i === 1 || i === 2 ? 'dim' : '');
          const strong = c.key === state.sortKey ? ' score' : '';
          return "<td class='" + cls + strong + "'>" + display(p, c) + '</td>';
        }).join('') + '</tr>'
      ).join('');
    }
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
    // the league switcher is meaningless on the continental tab
    const lgs = document.querySelector('nav.lgswitch');
    if (lgs) lgs.style.display = name === 'europe' ? 'none' : '';
  }
  // keep the hash mirroring the current league + tab so a season switch
  // (the dropdown carries location.hash across) can restore both
  // push=true records a step the reader took, so Back returns to it. Anything
  // the page does to itself -- normalising on load, resettling after a league
  // change -- replaces instead, or Back would walk through states nobody chose
  window.syncHash = function (push) {
    if (window.__navRestoring) return;   // mid-popstate: the hash is the target
    const parts = [];
    if (window.CUR_LG && document.querySelector('nav.lgswitch button'))
      parts.push('lg=' + window.CUR_LG.replace(/ /g, '_'));
    const active = document.querySelector("nav.tabs button[aria-selected='true']");
    if (active) parts.push(active.dataset.panel);
    // which match is open, so a refresh or a pasted link comes back to it.
    // Written only on the matches panel: the team deep link parses whatever
    // follows lg=, and an m= trailing it there would end up inside a name
    if (active && active.dataset.panel === 'fixtures' && window.currentMatch) {
      const id = window.currentMatch();
      if (id) parts.push('m=' + id);
    }
    const url = '#' + parts.join('&');
    if (url === decodeURIComponent(location.hash)) return;  // no history churn
    try {
      if (push) history.pushState(null, '', url);
      else history.replaceState(null, '', url);
    } catch (e) {
      // some browsers refuse pushState on a file:// document; this page is
      // meant to work opened straight off disk, so fall back to writing the
      // hash. Back then still works, one entry per move, same as before
      location.hash = url.slice(1);
    }
  };
  tabs.forEach((b) => b.addEventListener('click', () => {
    activate(b.dataset.panel);
    window.syncHash(true);
  }));
  // so a fixture row on the League tab can send the reader to the explorer
  window.showPanel = function (name, silent) {
    if (!document.getElementById('panel-' + name)) return false;
    activate(name);
    if (!silent) window.syncHash(true);
    return true;
  };
  const initial = decodeURIComponent(location.hash.slice(1)).split('&')
    .filter((s) => s && !s.includes('='))[0] || '';
  activate(document.getElementById('panel-' + initial) ? initial : tabs[0].dataset.panel);
})();

(function () {  // player profile cards + radar comparison
  if (typeof PLAYERS_BY_LG === 'undefined') return;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  let PLAYERS = PLAYERS_BY_LG[window.CUR_LG] || [];
  // flat list over every league: the comparison search is cross-league
  const ALL = [];
  Object.keys(PLAYERS_BY_LG).forEach((lg) => {
    PLAYERS_BY_LG[lg].forEach((p) => { p.lg = lg; ALL.push(p); });
  });
  function rebuildList() {
    $('pc-list').innerHTML = ALL.map((p) =>
      '<option value="' + esc(p.name) + ' \\u2014 ' + esc(p.team) + ' \\u00b7 ' + esc(p.lg) + '"></option>').join('');
  }
  const per90 = (p, k) => p.min > 0 ? p[k] * 90 / p.min : 0;
  const posOf = (p) => p.pos.includes('GK') ? 'GK' : ((p.pos.match(/[DMF]/) || ['F'])[0]);
  const POS_NAME = { GK: 'goalkeepers', D: 'defenders', M: 'midfielders', F: 'forwards' };
  const MIN_PEER = 450;
  // the reduced set for a feed without xGChain, xGBuildup or a penalty split
  const FOTMOB_METRICS = [
    { key: 'xg',      label: 'xG' },
    { key: 'goals',   label: 'Goals' },
    { key: 'shots',   label: 'Shots' },
    { key: 'sot',     label: 'On target' },
    { key: 'xgot',    label: 'xGOT' },
    { key: 'xa',      label: 'xA' },
    { key: 'assists', label: 'Assists' },
    { key: 'kp',      label: 'Chances created' }
  ];
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

  // 450 minutes is five full matches, and in August nobody has played five
  // matches: the filter then returns nobody, every percentile divides by zero,
  // and the card reads "NaNth" with empty bars. Hold the bar at 450 once the
  // season is old enough to clear it, and before that scale it to how much
  // football has actually been played
  // a league the explorer does not cover still ships its players, for the
  // club card's squad list. That is enough for a reduced profile, so a name
  // there is not dead text -- which is exactly how it read before: underlined
  // like every other link on the page, and inert.
  const FM_POOL = {};
  function fotmobPool(league) {
    if (FM_POOL[league]) return FM_POOL[league];
    const byClub = (typeof SQUADS_BY_LG === 'undefined' ? {} : SQUADS_BY_LG[league]) || {};
    const out = [];
    Object.keys(byClub).forEach((club) => {
      byClub[club].forEach((r) => {
        if (r[8] != null) out.push(fotmobRow(r, club, league));
      });
    });
    FM_POOL[league] = out;
    return out;
  }
  function fotmobRow(r, club, league) {
    return {
      feed: 'fotmob', name: r[0], pos: '', team: club, lg: league,
      games: r[2], min: r[3], goals: r[4], xg: r[5], assists: r[6], xa: r[7],
      id: r[8], shots: r[9], sot: r[10], kp: r[11], xgot: r[12],
      gdiff: Math.round((r[4] - r[5]) * 100) / 100,
      adiff: Math.round((r[6] - r[7]) * 100) / 100
    };
  }
  function poolOf(p) {
    if (p.feed === 'fotmob') return fotmobPool(p.lg);
    return PLAYERS_BY_LG[p.lg] || PLAYERS;
  }
  function peerFloor(pool) {
    let most = 0;
    pool.forEach((q) => { if (q.min > most) most = q.min; });
    return most >= MIN_PEER ? MIN_PEER : Math.max(45, Math.round(most * 0.5));
  }
  function peersOf(p) {
    // percentiles are always vs peers in the player's own league, so a profile
    // reads the same here as on the player's card
    const pool = poolOf(p);
    const floor = peerFloor(pool);
    // a feed with no position column ranks against everyone who has played
    // enough, which is the honest reading of what it can support
    if (!p.pos) return pool.filter((q) => q.min >= floor);
    let peers = pool.filter((q) => q.min >= floor && posOf(q) === posOf(p));
    if (peers.length < 10) peers = pool.filter((q) => q.min >= floor && posOf(q) !== 'GK');
    return peers;
  }
  function percentile(p, key, peers) {
    if (!peers.length) return null;      // nothing to rank against, so say so
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
    // accept both "Name" and the datalist's "Name — Team · League" form;
    // same-named players are disambiguated by team, then by current league
    const name = s.split(' \\u2014 ')[0].trim();
    const team = (s.split(' \\u2014 ')[1] || '').split(' \\u00b7 ')[0].trim();
    const cands = ALL.filter((q) => q.name === name);
    return cands.find((q) => q.team === team) ||
           cands.find((q) => q.lg === window.CUR_LG) || cands[0];
  };

  /* ---- career strip ---- */
  function careerBlock(p) {
    const C = (typeof CAREERS === 'undefined' ? null : CAREERS);
    const rows = C && C.p ? C.p[p.id] : null;
    if (!rows || !rows.length) return '';
    const seasons = C.s, leagues = C.l, clubs = C.c;
    const played = {};
    rows.forEach((r) => { (played[r[0]] = played[r[0]] || []).push(r); });
    let maxAbs = 3;
    rows.forEach((r) => { maxAbs = Math.max(maxAbs, Math.abs(r[5] - r[6])); });
    // the strip runs from the player's first stored season to the newest the
    // page can see, so the seasons he was elsewhere are a row rather than a
    // silence -- Understat covers the big five only, and a gap is that, not
    // a year out of football
    let first = seasons.length;
    rows.forEach((r) => { first = Math.min(first, r[0]); });
    let body = '';
    let gap = [];
    const flushGap = () => {
      if (!gap.length) return;
      const span = gap.length === 1 ? seasons[gap[0]]
        : seasons[gap[0]] + '\u2013' + seasons[gap[gap.length - 1]];
      body += "<tr class='car-gap'><td>" + esc(span) + "</td><td colspan='7'>not in " +
        'the big five' + (gap.length > 1 ? ' (' + gap.length + ' seasons)' : '') +
        '</td></tr>';
      gap = [];
    };
    for (let s = first; s < seasons.length; s++) {
      const stints = played[s];
      if (!stints) { gap.push(s); continue; }
      flushGap();
      stints.forEach((r, i) => {
        const lg = leagues[r[1]], gdiff = Math.round((r[5] - r[6]) * 10) / 10;
        const wide = Math.round(38 * Math.min(1, Math.abs(gdiff) / maxAbs));
        const side = gdiff >= 0 ? 'over' : 'under';
        // a mid-season move repeats the season, so only the first row names it
        body += "<tr" + (lg === p.lg ? '' : " class='car-away'") + '>' +
          '<td>' + (i ? '' : esc(seasons[s])) + '</td>' +
          "<td class='car-grow'>" + esc(clubs[r[2]]) +
          (lg === p.lg ? '' : " <span class='dim'>" + esc(lg) + '</span>') + '</td>' +
          "<td class='num dim'>" + r[3] + '</td>' +
          "<td class='num dim'>" + r[4] + '</td>' +
          "<td class='num score'>" + r[5] + '</td>' +
          "<td class='num dim'>" + r[6].toFixed(1) + '</td>' +
          "<td class='num'>" + r[7] + "<span class='dim'>/" + r[8].toFixed(1) + '</span></td>' +
          "<td class='num'><span class='hist-bar' style='width:80px'><i class='" + side +
          "' style='" + (gdiff >= 0 ? 'left:50%;width:' : 'right:50%;width:') + wide +
          "px'></i></span><span class='hist-n " + side + "'>" + signed(gdiff) +
          '</span></td></tr>';
      });
    }
    flushGap();
    return "<div class='pd-career'><div class='h2h-h'>Career " +
      "<span class='dim'>\u00b7 every season Understat has stored</span></div>" +
      "<div style='overflow-x:auto'><table><thead><tr><th>Season</th><th>Club</th>" +
      "<th class='num'>Apps</th><th class='num'>Min</th><th class='num'>G</th>" +
      "<th class='num'>xG</th><th class='num'>A/xA</th>" +
      "<th class='num'>G \u2212 xG</th></tr></thead><tbody>" + body +
      '</tbody></table></div></div>';
  }

  /* ---- profile card ---- */
  const overlay = $('pd-overlay');
  // it is authored inside the Players panel, and every other panel hides that
  // one with display:none -- which stops a fixed-position descendant being
  // rendered at all. Opening a card from the club card's squad list or from
  // search then set hidden=false on an element with no box: nothing appeared,
  // and nothing in the console said why. Lift it to the body, where a modal
  // belongs, so it is visible from whichever tab opened it
  document.body.appendChild(overlay);
  function closeDetail() { overlay.hidden = true; }
  function openDetail(p) {
    const fm = p.feed === 'fotmob';
    const peers = peersOf(p);
    const bars = (fm ? FOTMOB_METRICS : METRICS).map((m) => {
      const pct = percentile(p, m.key, peers);
      const cls = pct >= 70 ? 'hi' : pct >= 40 ? 'mid' : 'lo';
      return "<div class='pd-row'><span class='pd-label'>" + m.label + " /90</span>" +
        "<div class='pd-track'><div class='pd-fill " + cls + "' style='width:" +
        (pct == null ? 0 : pct) + "%'></div></div>" +
        "<span class='pd-val'>" + per90(p, m.key).toFixed(2) + " <em>" +
        (pct == null ? '\u2013' : ord(pct)) + "</em></span></div>";
    }).join('');
    const totals = (fm
      ? [['Goals', p.goals], ['Assists', p.assists], ['Shots', p.shots],
         ['Chances created', p.kp], ['G\\u2212xG', signed(p.gdiff)],
         ['A\\u2212xA', signed(p.adiff)]]
      : [['Goals', p.goals], ['Assists', p.assists], ['Shots', p.shots],
         ['Key passes', p.kp], ['G\\u2212xG', signed(p.gdiff)],
         ['A\\u2212xA', signed(p.adiff)]]
    ).map(([l, v]) =>
      "<div><span class='pd-tv'>" + v + "</span><span class='pd-tl'>" + l + "</span></div>"
    ).join('');
    $('pd-modal').innerHTML =
      "<div class='pd-head'><div><h4>" + esc(p.name) + "</h4>" +
      "<p class='meta'>" + esc(p.team) + " \\u00b7 " + esc(p.lg) +
      (p.pos ? " \\u00b7 " + esc(p.pos) : '') + " \\u00b7 " +
      p.games + " apps, " + p.min + " min</p></div>" +
      "<button id='pd-close' aria-label='Close'>\\u2715</button></div>" +
      "<div class='pd-totals'>" + totals + "</div>" +
      "<p class='meta'>Season totals above; bars below are per-90 rates as percentiles vs the " +
      peers.length + ' ' + esc(p.lg) + " " +
      (p.pos ? (POS_NAME[posOf(p)] || 'players') : 'players') + " with " +
      peerFloor(poolOf(p)) + "+ minutes this season.</p>" +
      bars + (fm
        // no career strip and no radar: both need the columns this feed does
        // not publish, and a card that says so beats one that invents them
        ? "<p class='meta'>FotMob publishes no position, xGChain, xGBuildup or " +
          'non-penalty split for this league, so the radar comparison and the ' +
          'career strip stay on the big five.</p>'
        : careerBlock(p) +
          "<button id='pd-compare' type='button'>Add to comparison</button>");
    overlay.hidden = false;
    $('pd-close').onclick = closeDetail;
    if ($('pd-compare')) $('pd-compare').onclick = () => { addToCompare(p); closeDetail(); };
  }
  // the club card's squad list lives in another closure and holds ids, not
  // player objects; this is the only way in
  window.showPlayer = function (league, id) {
    const pool = PLAYERS_BY_LG[league] || [];
    let p = pool.find((q) => String(q.id) === String(id));
    // the explorer first, then the squad rows shipped for leagues it does not
    // cover -- ids from the two feeds never meet, since a league is in one or
    // the other
    if (!p) p = fotmobPool(league).find((q) => String(q.id) === String(id));
    if (!p) return false;
    openDetail(p);
    return true;
  };
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
        AX.map((_, i) => pt(i, R * ring / 100)).join(' ') + "'/>";
    });
    AX.forEach((m, i) => {
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
      const pts = RADAR.map((m, j) =>
        pt(j, R * (percentile(p, m.key, peers) || 0) / 100)).join(' ');
      parts += "<polygon class='radar-poly pc" + i + "' points='" + pts + "'><title>" +
        esc(p.name) + "</title></polygon>";
    });
    return "<svg viewBox='0 0 " + W + " " + H + "' width='100%' style='max-width:520px;display:block;margin:0 auto' " +
      "role='img' aria-label='Radar comparison of selected players'>" + parts + "</svg>";
  }
  function compareTable(ps) {
    const head = "<tr><th>per 90 (percentile)</th>" +
      ps.map((p, i) => "<th class='num'><span class='pc-dot pc" + i + "'></span>" + esc(p.name) + "</th>").join('') + '</tr>';
    const rows = axesOf(RADAR, ts).map((m) =>
      '<tr><td>' + m.label + '</td>' + ps.map((p) => {
        const peers = peersOf(p);
        return "<td class='num'>" + per90(p, m.key).toFixed(2) +
          " <span class='dim'>(" +
          (percentile(p, m.key, peers) == null ? '\u2013'
            : ord(percentile(p, m.key, peers))) + ')</span></td>';
      }).join('') + '</tr>'
    ).join('');
    const info = "<tr><td class='dim'>Team \\u00b7 league \\u00b7 pos \\u00b7 minutes</td>" + ps.map((p) =>
      "<td class='num dim'>" + esc(p.team) + " \\u00b7 " + esc(p.lg) + " \\u00b7 " + esc(p.pos) + " \\u00b7 " + p.min + "'</td>"
    ).join('') + '</tr>';
    return "<div style='overflow-x:auto'><table>" + head + rows + info + '</table></div>';
  }
  function renderCompare() {
    const seen = new Set();
    const ps = [1, 2, 3].map((i) => byName(($('pc-' + i).value || '').trim()))
      .filter((p) => p && !seen.has(p) && seen.add(p)).slice(0, 3);
    const card = $('pc-card'), empty = $('pc-empty');
    if (ps.length < 2) { card.hidden = true; empty.hidden = false; return; }
    empty.hidden = true; card.hidden = false;
    const legend = "<div class='pc-legend'>" + ps.map((p, i) =>
      "<span><span class='pc-dot pc" + i + "'></span>" + esc(p.name) +
      " <span class='dim'>(" + esc(p.lg) + ' ' + (POS_NAME[posOf(p)] || '') + ")</span></span>").join('') + '</div>';
    card.innerHTML = legend + radarSvg(ps) + compareTable(ps);
  }
  function addToCompare(p) {
    const inputs = [1, 2, 3].map((i) => $('pc-' + i));
    const target = inputs.find((el) => !byName(el.value.trim())) || inputs[2];
    target.value = p.name + ' \\u2014 ' + p.team + ' \\u00b7 ' + p.lg;
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
    // the comparison is cross-league, so it survives a league switch;
    // PLAYERS only backs the explorer's row-click -> profile card mapping
    PLAYERS = PLAYERS_BY_LG[window.CUR_LG] || [];
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
  let HIST = (typeof HIST_BY_LG === 'undefined' ? {} : HIST_BY_LG)[window.CUR_LG] || {};
  let SQUADS = (typeof SQUADS_BY_LG === 'undefined' ? {} : SQUADS_BY_LG)[window.CUR_LG] || {};
  let TM = TM_BY_LG[window.CUR_LG] || {};
  let FORM = (typeof FORM_BY_LG === 'undefined' ? {} : FORM_BY_LG)[window.CUR_LG] || {};

  /* ---- recent form: what the percentiles above were actually built from ---- */
  const FMONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function fDate(iso) {
    const p = String(iso || '').split('-');
    return p.length < 3 ? (iso || '')
      : Number(p[2]) + ' ' + FMONTHS[Number(p[1]) - 1] + ' ' + p[0].slice(2);
  }
  const fNum = (v, d) => (v == null ? '\\u2013' : Number(v).toFixed(d));
  const fRes = (gf, ga) => (gf > ga ? 'W' : gf < ga ? 'L' : 'D');

  // the radar answers "what kind of side is this"; it cannot answer "how is it
  // going", and a profile reached by clicking a league-table row is usually
  // opened with the second question in mind
  function formBlock(name) {
    const rows = FORM[name] || [];
    if (!rows.length) return '';
    // oldest-first chips, the way a form guide is read, over a newest-first table
    const chips = rows.slice().reverse().map((r) => {
      const l = fRes(r[3], r[4]);
      return "<span class='chip " + l + "'>" + l + '</span>';
    }).join('');
    let pts = 0;
    rows.forEach((r) => { pts += r[3] > r[4] ? 3 : r[3] === r[4] ? 1 : 0; });
    let body = "<table class='fx-form'><tbody>";
    rows.forEach((r) => {
      const l = fRes(r[3], r[4]);
      body += "<tr><td><span class='chip " + l + "'>" + l + '</span></td>' +
        "<td class='dim'>" + fDate(r[0]) + '</td>' +
        "<td class='dim'>" + (r[1] === 'h' ? 'H' : 'A') + '</td>' +
        "<td class='fx-grow'>" + esc(r[2]) + '</td>' +
        "<td class='num score'>" + r[3] + '\\u2013' + r[4] + '</td>' +
        "<td class='num dim' title='expected goals that day'>" +
          fNum(r[5], 2) + '\\u2013' + fNum(r[6], 2) + '</td></tr>';
    });
    // chips run oldest to newest, the way a form guide is read; the table
    // under them runs newest first, the way a results list is
    return "<div class='tc-form'><div class='h2h-h'>Recent form</div>" +
      "<div class='tc-chips'>" + chips + "<span class='dim'>oldest to newest " +
      '\\u00b7 ' + pts + ' of ' + rows.length * 3 + ' points</span></div>' +
      body + '</tbody></table></div>';
  }

  // signed, with a real minus sign: a column of these is read by shape as
  // much as by value, and a hyphen next to a digit does not read as negative
  function hSig(v, dec) {
    const s = v > 0 ? '+' : v < 0 ? '\u2212' : '';
    return s + Math.abs(v).toFixed(dec);
  }

  function historyBlock(name) {
    const seasons = HIST.seasons || [];
    const rows = (HIST.clubs || {})[name] || [];
    // one season is not a history, and the strip would just restate the radar
    if (rows.length < 2) return '';
    const by = {};
    rows.forEach((r) => { by[r[0]] = r; });
    // scaled to this club's own biggest gap, with a floor so a steady side
    // does not get a row of dramatic-looking bars over three points
    let maxAbs = 8;
    rows.forEach((r) => { maxAbs = Math.max(maxAbs, Math.abs(r[9] - r[10])); });
    const last = seasons[seasons.length - 1];
    let body = '';
    // a run of absent seasons collapses to one row: a club promoted in 2020
    // would otherwise open its history with six identical empty lines, which
    // buries the seasons it did play without saying anything six times over
    let gap = [];
    const flushGap = () => {
      if (!gap.length) return;
      const span = gap.length === 1 ? gap[0] : gap[0] + '\u2013' + gap[gap.length - 1];
      body += "<tr class='hist-gap'><td>" + esc(span) + "</td><td colspan='8'>not in " +
        'this division' + (gap.length > 1 ? ' (' + gap.length + ' seasons)' : '') +
        '</td></tr>';
      gap = [];
    };
    seasons.forEach((s) => {
      const r = by[s];
      if (!r) { gap.push(s); return; }
      flushGap();
      const pos = r[1], of = r[2], mp = r[3], w = r[4], d = r[5], l = r[6];
      const gd = r[7], xgd = r[8], pts = r[9], xpts = r[10];
      // a full campaign is a double round robin; short of that, on the newest
      // season a page can see, the row is a season in progress rather than a
      // finished one. Only the newest: 2019/20 Ligue 1 was abandoned at 28
      // rounds and is finished all the same
      const live = s === last && mp < (of - 1) * 2;
      const delta = Math.round((pts - xpts) * 10) / 10;
      const wide = Math.round(46 * Math.min(1, Math.abs(delta) / maxAbs));
      const side = delta >= 0 ? 'over' : 'under';
      const bar = "<span class='hist-bar'><i class='" + side + "' style='" +
        (delta >= 0 ? 'left:50%;width:' : 'right:50%;width:') + wide + "px'></i></span>";
      body += '<tr' + (live ? " class='hist-now'" : '') + '>' +
        '<td>' + esc(s) + (live ? " <span class='dim'>so far</span>" : '') + '</td>' +
        "<td class='num'>" + pos + "<span class='dim'>/" + of + '</span></td>' +
        "<td class='num dim'>" + mp + '</td>' +
        "<td class='num dim'>" + w + '\u2013' + d + '\u2013' + l + '</td>' +
        "<td class='num'>" + hSig(gd, 0) + '</td>' +
        "<td class='num dim'>" + hSig(xgd, 1) + '</td>' +
        "<td class='num score'>" + pts + '</td>' +
        "<td class='num dim'>" + xpts.toFixed(1) + '</td>' +
        "<td class='num'>" + bar + "<span class='hist-n " + side + "'>" +
        hSig(delta, 1) + '</span></td></tr>';
    });
    flushGap();
    return "<div class='tc-hist'><div class='h2h-h'>Season by season " +
      "<span class='dim'>\u00b7 points against expected points, oldest first" +
      '</span></div>' +
      "<div style='overflow-x:auto'><table class='fx-form'><thead><tr>" +
      "<th>Season</th><th class='num'>Pos</th><th class='num'>P</th>" +
      "<th class='num'>W\u2013D\u2013L</th><th class='num'>GD</th>" +
      "<th class='num' title='expected goal difference'>xGD</th>" +
      "<th class='num'>Pts</th><th class='num' title='expected points'>xPts</th>" +
      "<th class='num'>Pts \u2212 xPts</th></tr></thead><tbody>" +
      body + '</tbody></table></div></div>';
  }

  function squadRows(name) {
    // the big five are already on the page as PLAYERS_BY_LG, one entry per
    // player with the club they play for, so the squad is a filter rather
    // than a second copy of the same data. Only a league the explorer does
    // not cover needs rows shipped for it
    const pool = (typeof PLAYERS_BY_LG === 'undefined'
      ? null : PLAYERS_BY_LG[window.CUR_LG]);
    if (pool && pool.length) {
      return pool.filter((p) => p.team === name && p.min > 0)
        .map((p) => [p.name, p.pos || '', p.games, p.min, p.goals, p.xg,
                     p.assists, p.xa, p.id])
        .sort((a, b) => b[3] - a[3]);
    }
    return (SQUADS[name] || []).slice();
  }

  function squadBlock(name) {
    const rows = squadRows(name);
    if (!rows.length) return '';
    // the count is players who have *appeared*, not a registered squad: neither
    // feed carries a player until they have played. One matchday in, that is
    // the eleven and its substitutes, which reads like missing data unless the
    // heading says what it is counting
    const t = byTeam(name), n = t ? t.mp : 0;
    const mp = n ? n + (n === 1 ? ' match' : ' matches') : 'the season';
    // an id is necessary but not sufficient: the frozen season pages ship
    // squads without the player explorer that owns the profile card, and an
    // underlined name that answers nothing is worse than plain text
    const live = rows.some((r) => r[8] != null) &&
      typeof window.showPlayer === 'function';
    let body = '';
    rows.forEach((r) => {
      const gdiff = Math.round((r[4] - r[5]) * 10) / 10;
      const side = gdiff >= 0 ? 'over' : 'under';
      body += '<tr>' +
        "<td class='fx-grow'>" + (!live || r[8] == null ? esc(r[0])
          : "<span class='squad-link' data-pid='" + esc(r[8]) + "'>" +
            esc(r[0]) + '</span>') + '</td>' +
        "<td class='dim'>" + esc(r[1] || '\u2013') + '</td>' +
        "<td class='num dim'>" + r[2] + '</td>' +
        "<td class='num dim'>" + r[3] + '</td>' +
        "<td class='num score'>" + r[4] + '</td>' +
        "<td class='num dim'>" + r[5].toFixed(1) + '</td>' +
        "<td class='num'>" + r[6] + "<span class='dim'>/" + r[7].toFixed(1) +
        '</span></td>' +
        "<td class='num hist-n " + side + "'>" + hSig(gdiff, 1) + '</td></tr>';
    });
    return "<div class='tc-squad'><div class='h2h-h'>Squad " +
      "<span class='dim'>\u00b7 " + rows.length + ' players used in ' + mp + ', most minutes first' +
      (live ? ' \u00b7 click a name for their profile' +
        (rows[0].length > 9 ? '' : ' and career') : '') +
      '</span></div>' +
      "<div style='overflow-x:auto'><table class='fx-form'><thead><tr>" +
      "<th>Player</th><th>Pos</th><th class='num'>Apps</th>" +
      "<th class='num'>Min</th><th class='num'>G</th><th class='num'>xG</th>" +
      "<th class='num'>A/xA</th><th class='num'>G \u2212 xG</th>" +
      '</tr></thead><tbody>' + body + '</tbody></table></div></div>';
  }

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
  // FotMob-backed leagues (Allsvenskan) have no PPDA/deep completions, so
  // any axis where a selected team is missing the number is dropped
  const axesOf = (list, ts) => list.filter((m) => ts.every((t) => t[m.key] != null));
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
    const AX = axesOf(RADAR, ts);
    if (AX.length < 3) return '';
    const W = 460, H = 350, cx = W / 2, cy = H / 2 + 6, R = 118, N = AX.length;
    const pt = (i, r) => {
      const a = -Math.PI / 2 + i * 2 * Math.PI / N;
      return (cx + r * Math.cos(a)).toFixed(1) + ',' + (cy + r * Math.sin(a)).toFixed(1);
    };
    let parts = '';
    [25, 50, 75, 100].forEach((ring) => {
      parts += "<polygon class='radar-grid' points='" +
        AX.map((_, i) => pt(i, R * ring / 100)).join(' ') + "'/>";
    });
    AX.forEach((m, i) => {
      parts += "<line class='radar-axis' x1='" + cx + "' y1='" + cy + "' x2='" +
        pt(i, R).replace(',', "' y2='") + "'/>";
      const a = -Math.PI / 2 + i * 2 * Math.PI / N;
      const lx = cx + (R + 16) * Math.cos(a), ly = cy + (R + 16) * Math.sin(a);
      const anchor = Math.abs(Math.cos(a)) < 0.3 ? 'middle' : (Math.cos(a) > 0 ? 'start' : 'end');
      parts += "<text x='" + lx.toFixed(0) + "' y='" + (ly + 4).toFixed(0) +
        "' text-anchor='" + anchor + "'>" + m.label + "</text>";
    });
    ts.forEach((t, i) => {
      const pts = AX.map((m, j) => pt(j, R * pct(t, m) / 100)).join(' ');
      parts += "<polygon class='radar-poly pc" + i + "' points='" + pts + "'><title>" +
        esc(t.team) + "</title></polygon>";
    });
    return "<svg viewBox='0 0 " + W + " " + H + "' width='100%' style='max-width:520px;display:block;margin:0 auto' " +
      "role='img' aria-label='Radar comparison of selected teams'>" + parts + "</svg>";
  }
  function compareTable(ts) {
    const head = "<tr><th>metric (league percentile)</th>" +
      ts.map((t, i) => "<th class='num'><span class='pc-dot pc" + i + "'></span>" + esc(t.team) + "</th>").join('') + '</tr>';
    const rows = axesOf(RADAR, ts).map((m) =>
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

  /* ---- head-to-head deep dive: shown when exactly two teams are picked ---- */
  const TAPE = RADAR.concat([
    { key: 'pts',  label: 'Points',          unit: 'season total', dec: 0 },
    { key: 'xpts', label: 'Expected points', unit: 'season total', dec: 1 },
    { key: 'gpm',  label: 'Goals',           unit: 'per match',    dec: 2 },
    { key: 'cpm',  label: 'Conceded',        unit: 'per match',    dec: 2, invert: true }
  ]);
  function tapeHtml(a, b) {
    const rows = axesOf(TAPE, [a, b]).map((m) => {
      const pa = pct(a, m), pb = pct(b, m);
      const shareA = pa + pb === 0 ? 50 : Math.round(100 * pa / (pa + pb));
      const cell = (t, p, side, lead) =>
        "<span class='h2h-val " + side + (lead ? ' lead' : '') + "'>" + fmt(t, m) +
        " <span class='dim'>(" + ord(p) + ")</span></span>";
      return "<div class='h2h-metric'><div class='h2h-lab'>" + m.label +
        ' \\u00b7 ' + m.unit + "</div><div class='h2h-row'>" +
        cell(a, pa, 'a', pa > pb) +
        "<div class='h2h-bar'><i class='a' style='width:" + shareA + "%'></i>" +
        "<i class='b' style='width:" + (100 - shareA) + "%'></i></div>" +
        cell(b, pb, 'b', pb > pa) + '</div></div>';
    }).join('');
    return "<div class='h2h-h'>Tale of the tape <span class='dim'>" +
      '\\u00b7 bars split by league percentile</span></div>' + rows;
  }
  function meetingsHtml(a, b) {
    const A = TM[a.team] || [], B = TM[b.team] || [];
    const rows = [];
    A.forEach((m) => {
      // the fixture appears in both teams' lists: same date, opposite venue,
      // mirrored score AND mirrored xG (score alone can collide on a shared matchday)
      const hit = B.some((q) => q[0] === m[0] && q[1] !== m[1] &&
        q[2] === m[3] && q[3] === m[2] && q[4] === m[5] && q[5] === m[4]);
      if (!hit) return;
      const home = m[1] === 'h' ? a : b, away = m[1] === 'h' ? b : a;
      const gs = m[1] === 'h' ? [m[2], m[3]] : [m[3], m[2]];
      const xs = m[1] === 'h' ? [m[4], m[5]] : [m[5], m[4]];
      rows.push("<tr><td class='dim'>" + m[0] + '</td><td>' + esc(home.team) +
        " <span class='score'>" + gs[0] + '\\u2013' + gs[1] + '</span> ' + esc(away.team) +
        "</td><td class='num dim'>xG " + xs[0].toFixed(2) + '\\u2013' + xs[1].toFixed(2) +
        '</td></tr>');
    });
    const body = rows.length
      ? "<div style='overflow-x:auto'><table>" + rows.join('') + '</table></div>'
      : "<p class='dim'>No meetings between these two so far this season.</p>";
    return "<div class='h2h-h'>Meetings this season</div>" + body;
  }
  function formHtml(a, b) {
    const chips = (t) => (TM[t.team] || []).slice(-5).map((m) => {
      const r = m[7] === 3 ? 'W' : m[7] === 1 ? 'D' : 'L';
      return "<span class='chip " + r + "'>" + r + '</span>';
    }).join('');
    const line = (t, i) => "<p style='margin:7px 0'><span class='pc-dot pc" + i + "'></span>" +
      esc(t.team) + ' \\u00a0' + chips(t) + '</p>';
    return "<div class='h2h-h'>Last five matches <span class='dim'>\\u00b7 newest right</span></div>" +
      line(a, 0) + line(b, 1);
  }
  function splitHtml(a, b) {
    const agg = (t, ha) => {
      const l = (TM[t.team] || []).filter((m) => m[1] === ha);
      if (!l.length) return ['\\u2013', '\\u2013'];
      const pts = l.reduce((s, m) => s + m[7], 0) / l.length;
      const nd = l.reduce((s, m) => s + m[6], 0) / l.length;
      return [pts.toFixed(2), ((nd > 0 ? '+' : '') + nd.toFixed(2)).replace('-', '\\u2212')];
    };
    const row = (t, i) => {
      const h = agg(t, 'h'), aw = agg(t, 'a');
      return "<tr><td><span class='pc-dot pc" + i + "'></span>" + esc(t.team) + '</td>' +
        [h[0], aw[0], h[1], aw[1]].map((v) => "<td class='num'>" + v + '</td>').join('') + '</tr>';
    };
    return "<div class='h2h-h'>Home / away split <span class='dim'>\\u00b7 per match</span></div>" +
      "<div style='overflow-x:auto'><table><tr><th></th><th class='num'>pts home</th>" +
      "<th class='num'>pts away</th><th class='num'>npxGD home</th><th class='num'>npxGD away</th></tr>" +
      row(a, 0) + row(b, 1) + '</table></div>';
  }
  function curveHtml(a, b) {
    const roll = (t) => {
      const ms = TM[t.team] || [];
      return ms.map((m, i) => {
        const s = ms.slice(Math.max(0, i - 5), i + 1);
        return { d: m[0], v: s.reduce((x, q) => x + q[6], 0) / s.length };
      });
    };
    const sa = roll(a), sb = roll(b);
    const n = Math.max(sa.length, sb.length);
    if (n < 2) return '';
    const W = 640, H = 222, pl = 40, pr = 12, ptop = 12, pbot = 34;
    const maxAbs = Math.max(0.5,
      ...sa.map((p) => Math.abs(p.v)), ...sb.map((p) => Math.abs(p.v))) * 1.08;
    const x = (i) => pl + (W - pl - pr) * i / (n - 1);
    const y = (v) => ptop + (H - ptop - pbot) * (1 - (v + maxAbs) / (2 * maxAbs));
    const line = (s, cls) => "<polyline class='h2h-line " + cls + "' points='" +
      s.map((p, i) => x(i).toFixed(1) + ',' + y(p.v).toFixed(1)).join(' ') + "'/>";
    const dots = (s, cls) => s.map((p, i) =>
      "<circle class='h2h-dot " + cls + "' cx='" + x(i).toFixed(1) + "' cy='" +
      y(p.v).toFixed(1) + "' r='2.4'><title>match " + (i + 1) + ' \\u00b7 ' + p.d +
      ' \\u00b7 ' + ((p.v > 0 ? '+' : '') + p.v.toFixed(2)).replace('-', '\\u2212') +
      '</title></circle>').join('');
    const lab = (v) => (v > 0 ? '+' : v < 0 ? '\\u2212' : '') + Math.abs(v).toFixed(1);
    let g = '';
    [maxAbs * 0.85, -maxAbs * 0.85].forEach((v) => {
      g += "<line class='gridline' x1='" + pl + "' y1='" + y(v).toFixed(1) +
        "' x2='" + (W - pr) + "' y2='" + y(v).toFixed(1) + "'/>";
    });
    g += "<line class='zeroline' x1='" + pl + "' y1='" + y(0).toFixed(1) +
      "' x2='" + (W - pr) + "' y2='" + y(0).toFixed(1) + "'/>";
    [maxAbs * 0.85, 0, -maxAbs * 0.85].forEach((v) => {
      g += "<text x='" + (pl - 6) + "' y='" + (y(v) + 4).toFixed(1) +
        "' text-anchor='end'>" + lab(v) + '</text>';
    });
    for (let i = 5; i <= n; i += 5) {
      g += "<line class='gridline' x1='" + x(i - 1).toFixed(1) + "' y1='" + (H - pbot) +
        "' x2='" + x(i - 1).toFixed(1) + "' y2='" + (H - pbot + 4) + "'/>" +
        "<text x='" + x(i - 1).toFixed(1) + "' y='" + (H - 18) +
        "' text-anchor='middle'>" + i + '</text>';
    }
    g += "<text x='" + ((pl + W - pr) / 2).toFixed(0) + "' y='" + (H - 4) +
      "' text-anchor='middle'>match number</text>";
    return "<div class='h2h-h'>Form curves <span class='dim'>\\u00b7 rolling 6-match " +
      'npxG difference \\u00b7 one dot per match, hover for date and value</span></div>' +
      "<svg viewBox='0 0 " + W + ' ' + H + "' width='100%' style='max-width:680px;display:block' " +
      "role='img' aria-label='Overlaid rolling form curves of both teams'>" +
      g + line(sa, 'a') + line(sb, 'b') + dots(sa, 'a') + dots(sb, 'b') + '</svg>';
  }
  function h2hHtml(a, b) {
    return tapeHtml(a, b) +
      "<div class='h2h-cols'><div>" + meetingsHtml(a, b) + '</div><div>' +
      formHtml(a, b) + splitHtml(a, b) + '</div></div>' + curveHtml(a, b);
  }

  function renderTC() {
    const seen = new Set();
    const ts = [1, 2, 3].map((i) => byTeam($('tc-' + i).value))
      .filter((t) => t && !seen.has(t.team) && seen.add(t.team)).slice(0, 3);
    const card = $('tc-card'), empty = $('tc-empty');
    // one team is a profile rather than a comparison, and worth rendering:
    // it is where a click on a club name from the League tab lands
    if (!ts.length) { card.hidden = true; empty.hidden = false; return; }
    empty.hidden = true; card.hidden = false;
    const legend = "<div class='pc-legend'>" + ts.map((t, i) =>
      "<span><span class='pc-dot pc" + i + "'></span>" + esc(t.team) +
      " <span class='dim'>(" + t.pts + " pts)</span></span>").join('') + '</div>';
    card.innerHTML = legend + radarSvg(ts) +
      (ts.length === 2 ? h2hHtml(ts[0], ts[1]) : compareTable(ts)) +
      // only on a single club: two teams already get recent form inside the
      // head-to-head, and three stacked form tables would bury the radar
      (ts.length === 1
        ? formBlock(ts[0].team) + historyBlock(ts[0].team) + squadBlock(ts[0].team)
        : '');
  }
  // entry point for a click on a club name over on the League tab
  window.showTeam = function (league, name) {
    if (league && league !== window.CUR_LG) {
      let moved = false;
      document.querySelectorAll('nav.lgswitch button').forEach((b) => {
        if (b.dataset.lg === league) { b.click(); moved = true; }
      });
      if (!moved && !TEAMS_BY_LG[league]) return false;
    }
    if (!byTeam(name)) return false;
    if (window.showPanel) window.showPanel('teams');
    $('tc-1').value = name;
    $('tc-2').value = '';
    $('tc-3').value = '';
    renderTC();
    $('tc-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
    return true;
  };
  window.hasTeam = (league, name) =>
    (TEAMS_BY_LG[league] || []).some((t) => t.team === name);

  $('tc-card').addEventListener('click', (e) => {
    const el = e.target.closest('.squad-link');
    if (el && window.showPlayer) window.showPlayer(window.CUR_LG, el.dataset.pid);
  });
  [1, 2, 3].forEach((i) => $('tc-' + i).addEventListener('change', renderTC));
  $('tc-clear').addEventListener('click', () => {
    [1, 2, 3].forEach((i) => { $('tc-' + i).value = ''; });
    renderTC();
  });
  document.addEventListener('leaguechange', () => {
    TEAMS = TEAMS_BY_LG[window.CUR_LG] || [];
    TM = TM_BY_LG[window.CUR_LG] || {};
    FORM = (typeof FORM_BY_LG === 'undefined' ? {} : FORM_BY_LG)[window.CUR_LG] || {};
    HIST = (typeof HIST_BY_LG === 'undefined' ? {} : HIST_BY_LG)[window.CUR_LG] || {};
    SQUADS = (typeof SQUADS_BY_LG === 'undefined' ? {} : SQUADS_BY_LG)[window.CUR_LG] || {};
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

(function () {  // fixture explorer
  if (typeof FIXTURES_BY_LG === 'undefined') return;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const card = $('fx-card'), pick = $('fx-pick');
  if (!card || !pick) return;
  // one list, both tenses: a played match renders as a report, an upcoming
  // one as a preview, and the dropdown groups them under their own headings
  let D = null, idx = 0, ENTRIES = [];
  // a match named in the URL wins over the default, once, on the first build
  const _m = /(?:^|&)m=([^&]+)/.exec(decodeURIComponent(location.hash.slice(1)));
  let wanted = _m ? _m[1] : null;

  const num = (v, d) => (v == null ? '\\u2013' : Number(v).toFixed(d));
  const sign = (v, d) => (v > 0 ? '+' : '') + num(v, d).replace('-', '\\u2212');
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function shortDate(iso) {
    const p = String(iso || '').split('-');
    if (p.length < 3) return iso || '';
    return Number(p[2]) + ' ' + MONTHS[Number(p[1]) - 1] + ' ' + p[0].slice(2);
  }
  // W/D/L from the perspective of the row's team
  const outcome = (gf, ga) => (gf > ga ? 'W' : gf < ga ? 'L' : 'D');

  function verdict(f) {
    if (!f.p) {
      const who = (f.nohist || []).map(esc).join(' and ');
      return "<div class='fx-noverdict'>No forecast for this one \\u2014 " + who +
        ' ' + ((f.nohist || []).length > 1 ? 'have' : 'has') +
        ' no top-flight xG history yet, so the model has nothing to build a ' +
        'strength estimate from. It declines rather than guessing.</div>';
    }
    const [h, d, a] = f.p, pc = f.pct;
    const seg = (cls, share, whole) => "<i class='" + cls + "' style='width:" +
      (share * 100).toFixed(1) + "%'>" + (share >= 0.15 ? whole + '%' : '') + '</i>';
    return "<div class='fx-verdict'>" +
      "<div class='prob' title='" + esc(f.home) + ' ' + pc[0] +
        '% \\u00b7 draw ' + pc[1] + '% \\u00b7 ' + esc(f.away) + ' ' +
        pc[2] + "%'>" + seg('h', h, pc[0]) + seg('d', d, pc[1]) + seg('a', a, pc[2]) + '</div>' +
      "<p class='meta'>Expected goals <b>" + num(f.lam[0], 1) + '\\u2013' +
        num(f.lam[1], 1) + '</b> \\u00b7 built on at least ' + f.n +
        ' matches per side \\u00b7 <span class="dim">a model, not a promise</span></p>' +
      '</div>';
  }

  function formStrip(name) {
    const rows = (D.form || {})[name] || [];
    if (!rows.length) {
      return "<p class='dim fx-none'>No stored match history for " + esc(name) + '.</p>';
    }
    let out = "<table class='fx-form'><tbody>";
    rows.forEach((r) => {
      const [date, ha, opp, gf, ga, xg, xga] = r;
      const res = outcome(gf, ga);
      out += '<tr><td>' + "<span class='chip " + res + "'>" + res + '</span></td>' +
        "<td class='dim'>" + shortDate(date) + '</td>' +
        "<td class='dim'>" + (ha === 'h' ? 'H' : 'A') + '</td>' +
        "<td class='fx-grow'>" + esc(opp) + '</td>' +
        "<td class='num score'>" + gf + '\\u2013' + ga + '</td>' +
        "<td class='num dim' title='expected goals that day'>" +
          num(xg, 2) + '\\u2013' + num(xga, 2) + '</td></tr>';
    });
    return out + '</tbody></table>';
  }

  function venueBox(name, side) {
    const rec = (D.venue || {})[name] || {};
    const label = side === 'h' ? 'at home' : 'away';
    const strip = (v, lab, cls) => {
      if (!v || !v[0]) return '';
      const [mp, gf, ga, xg, xga] = v;
      const cell = (val, l) => "<div><span class='pd-tv'>" + val +
        "</span><span class='pd-tl'>" + l + '</span></div>';
      return "<div class='pd-totals" + (cls || '') + "'>" +
        cell(mp, lab) + cell((gf / mp).toFixed(2), 'goals') +
        cell((ga / mp).toFixed(2), 'conceded') +
        cell((xg / mp).toFixed(2), 'xG') + cell((xga / mp).toFixed(2), 'xGA') +
        '</div>';
    };
    const here = strip(rec[side], label);
    const all = strip(rec.all, 'all matches', ' fx-all');
    if (!here && !all) {
      return "<p class='dim fx-none'>No matches in " +
        esc(rec.season || 'the stored season') + '.</p>';
    }
    if (!here) {
      return "<p class='dim fx-none'>No matches " + label + ' yet in ' +
        esc(rec.season || 'the stored season') + '.</p>' + all;
    }
    return here + all;
  }

  function h2hBlock(f) {
    const rows = f.h2h ? (D.h2h || {})[f.h2h] || [] : [];
    if (!rows.length) {
      return "<p class='dim fx-none'>No previous league meeting in the stored data " +
        '\\u2014 a first meeting at this level, or one of the clubs has no xG history yet.</p>';
    }
    // Deliberately neutral: this table is the two clubs' shared history, not
    // one of them having a good or bad run, so there are no win/loss chips
    // colouring a past meeting by whichever side happens to be at home in
    // the match on screen. The tally names both clubs instead of expressing
    // one side's record as W/D/L.
    let w = 0, dr = 0, l = 0;
    rows.forEach((r) => {
      // r = [date, homeIsFixtureHome, homeGoals, awayGoals, homeXg, awayXg]
      const [, isHome, hg, ag] = r;
      const gf = isHome ? hg : ag, ga = isHome ? ag : hg;
      if (gf > ga) w++; else if (gf < ga) l++; else dr++;
    });
    const wins = (n, who) => '<b>' + esc(who) + '</b> ' + n +
      (n === 1 ? ' win' : ' wins');
    let out = "<p class='meta'>" + wins(w, f.home) + ' \\u00b7 ' + dr +
      (dr === 1 ? ' draw' : ' draws') + ' \\u00b7 ' + wins(l, f.away) +
      " <span class=dim>in the last " + rows.length +
      (rows.length === 1 ? ' league meeting' : ' league meetings') + '</span></p>' +
      "<table class='fx-form fx-h2h'><tbody>";
    rows.forEach((r) => {
      const [date, isHome, hg, ag, hxg, axg] = r;
      const homeName = isHome ? f.home : f.away, awayName = isHome ? f.away : f.home;
      // the winning side's name carries the weight the chip used to
      const hw = hg > ag ? ' fx-won' : '', aw = ag > hg ? ' fx-won' : '';
      out += '<tr>' +
        "<td class='dim'>" + shortDate(date) + '</td>' +
        "<td style='text-align:right' class='fx-grow" + hw + "'>" + esc(homeName) + '</td>' +
        "<td class='num score'>" + hg + '\\u2013' + ag + '</td>' +
        "<td class='fx-grow" + aw + "'>" + esc(awayName) + '</td>' +
        "<td class='num dim' title='expected goals that day'>" +
          num(hxg, 2) + '\\u2013' + num(axg, 2) + '</td></tr>';
    });
    return out + '</tbody></table>';
  }

  function playerBox(name) {
    const rec = (D.players || {})[name] || {};
    const rows = rec.rows || [];
    if (!rows.length) {
      return "<p class='dim fx-none'>No player data stored for " + esc(name) + ' yet.</p>';
    }
    let out = rec.season
      ? "<p class='meta fx-season'>" + esc(rec.season) + '</p>' : '';
    out += "<table class='fx-form'><thead><tr><th class='fx-grow'>Player</th>" +
      "<th class='num'>Min</th><th class='num'>G</th><th class='num'>xG</th>" +
      "<th class='num'>A</th><th class='num'>xA</th></tr></thead><tbody>";
    rows.forEach((p) => {
      out += "<tr><td class='fx-grow'>" + esc(p[0]) + "</td><td class='num dim'>" + p[1] +
        "</td><td class='num'>" + p[2] + "</td><td class='num dim'>" + num(p[3], 1) +
        "</td><td class='num'>" + p[4] + "</td><td class='num dim'>" + num(p[5], 1) +
        '</td></tr>';
    });
    return out + '</tbody></table>';
  }

  // ---- match report, for a match already played ----------------------
  function chanceVerdict(m) {
    if (m.hxg == null || m.axg == null) return '';
    const d = m.hxg - m.axg, EVEN = 0.3;
    const won = m.hg > m.ag ? m.home : m.ag > m.hg ? m.away : null;
    const better = d > EVEN ? m.home : d < -EVEN ? m.away : null;
    let s;
    if (!better) {
      s = won ? esc(won) + ' won a match that was close on chance quality.'
              : 'Level on the pitch and level on chance quality.';
    } else if (!won) {
      s = esc(better) + ' created the better chances but it finished level.';
    } else if (better === won) {
      s = esc(won) + ' created the better chances and won.';
    } else {
      s = esc(better) + ' created the better chances; ' + esc(won) + ' won anyway.';
    }
    return "<p class='meta'>" + s + ' <span class="dim">One match of xG is a ' +
      'small sample \\u2014 chance quality describes the 90 minutes, it does not ' +
      'award the points.</span></p>';
  }

  // Understat reruns a finished match from the chances both sides created and
  // reports how often each side comes out on top. It is not a prediction --
  // it exists only after the match -- so it answers a different question from
  // the model's call: not "who wins" but "who deserved to".
  const probBar = (p, pct, cls) => {
    const seg = (c, share, whole) => "<i class='" + c + "' style='width:" +
      (share * 100).toFixed(1) + "%'>" + (share >= 0.15 ? whole + '%' : '') + '</i>';
    return "<div class='prob " + cls + "'>" + seg('h', p[0], pct[0]) +
      seg('d', p[1], pct[1]) + seg('a', p[2], pct[2]) + '</div>';
  };

  function sideName(m, i) {
    return i === 1 ? 'a draw' : esc(i === 0 ? m.home : m.away);
  }

  function deservedBox(m) {
    const won = m.hg > m.ag ? 0 : m.hg === m.ag ? 1 : 2;
    const top = m.fc.indexOf(Math.max.apply(null, m.fc));
    let read;
    if (m.fc[top] < 0.4) {
      read = 'Replayed from those chances the match splits close to evenly' +
        (won === 1 ? ', and it finished level.'
                   : ' — ' + sideName(m, won) + ' took the points from a ' +
                     'match that could have gone any way.');
    } else if (top === won) {
      read = 'Replayed from those chances, ' +
        (won === 1 ? 'it finishes level ' : sideName(m, top) + ' come out on top ') +
        m.fcpct[top] + '% of the time — the result the match deserved.';
    } else {
      read = 'Replayed from those chances, ' +
        (top === 1 ? 'it finishes level ' : sideName(m, top) + ' come out on top ') +
        m.fcpct[top] + '% of the time. ' +
        (won === 1 ? 'It finished level.' : sideName(m, won) + ' won it.');
    }
    return probBar(m.fc, m.fcpct, 'fc') +
      "<p class='meta'>" + read + " <span class='dim'>Understat's simulation " +
      'of the shots actually taken — it knows what the chances were, so ' +
      'it is a verdict on the 90 minutes, never a forecast of them.</span></p>';
  }

  // the point of showing both: a missed call on a match the chances say we
  // read correctly is a different failure from one the chances agree with
  function luckLine(m) {
    if (!m.fc || !m.pct) return '';
    const won = m.hg > m.ag ? 0 : m.hg === m.ag ? 1 : 2;
    const pick = m.pct.indexOf(Math.max.apply(null, m.pct));
    const top = m.fc.indexOf(Math.max.apply(null, m.fc));
    let s;
    if (m.fc[top] < 0.4) {
      // no lean worth the name: saying the chances "back" anything here would
      // read as agreement the numbers do not actually contain
      s = pick === won
        ? 'The model called it, though the chances split the match too evenly ' +
          'to say anyone deserved it.'
        : 'The model missed, but the chances split the match too evenly to say ' +
          'anyone deserved it either.';
    } else if (pick === won) {
      s = top === won
        ? 'The model called it, and the chances back the result.'
        : 'The model called it, but the chances lean towards ' + sideName(m, top) +
          ' — right about the winner, flattered by how it finished.';
    } else if (top === pick) {
      s = 'The model missed, and yet the chances lean the same way it did — ' +
        'a reading beaten by the finishing rather than by the football.';
    } else if (top === won) {
      s = 'The model missed, and the chances back the result.';
    } else {
      // the chances favour neither the call nor the winner -- usually a draw
      s = 'The model missed, but so did the chances: they lean towards ' +
        sideName(m, top) + ', which is not what happened either.';
    }
    return "<div class='fx-luck'>" + s + '</div>';
  }

  function callBox(m) {
    if (!m.pct) {
      return "<p class='dim fx-none'>No published call for this one \\u2014 it was " +
        'never in a predictions slate, usually because a club had no top-flight ' +
        'xG history at the time.</p>';
    }
    const outcome = m.hg > m.ag ? 0 : m.hg === m.ag ? 1 : 2;
    const top = m.pct.indexOf(Math.max.apply(null, m.pct));
    const names = [m.home, 'a draw', m.away];
    const hit = top === outcome;
    const badge = "<span class='fx-grade " + (hit ? 'ok' : 'no') + "'>" +
      (hit ? 'called it' : 'missed') + '</span>';
    const when = m.first
      ? (m.first === m.last
          ? 'written down on ' + shortDate(m.first)
          : 'first called ' + shortDate(m.first) + ', last updated ' + shortDate(m.last))
      : '';
    return probBar(m.p, m.pct, '') +
      "<p class='meta'>Leaned " + esc(names[top]) + ' at ' + m.pct[top] + '% ' + badge +
      (m.lam ? " <span class='dim'>\\u00b7 forecast " + num(m.lam[0], 1) + '\\u2013' +
        num(m.lam[1], 1) : "<span class='dim'>") +
      (when ? ' \\u00b7 ' + when : '') + '</span></p>';
  }

  function renderResult(m) {
    const when = shortDate(m.date) + (m.round ? ' \\u00b7 Round ' + m.round : '');
    const xg = (m.hxg != null && m.axg != null)
      ? "<div class='fx-xg'><span>Expected goals</span><b>" + num(m.hxg, 2) +
        ' \\u2013 ' + num(m.axg, 2) + '</b></div>'
      : "<p class='dim fx-none'>No xG stored for this match yet \\u2014 the feed " +
        'usually catches up within a day.</p>';
    card.innerHTML =
      "<div class='fx-head'><h4>" + esc(m.home) + " <span class='fx-score'>" +
        m.hg + ' \\u2013 ' + m.ag + '</span> ' + esc(m.away) +
        "</h4><span class='dim'>" + esc(when) + '</span></div>' +
      xg +
      // the forecast supersedes the xG-difference verdict rather than sitting
      // beside it: same question, and this one answers it properly
      (m.fc ? "<h4 class='fx-h'>What the chances deserved</h4>" + deservedBox(m)
            : chanceVerdict(m)) +
      "<h4 class='fx-h'>What the model said beforehand</h4>" + callBox(m) +
      luckLine(m) +
      "<h4 class='fx-h'>Head to head before this match</h4>" + h2hBlock(m);
  }

  function render() {
    const e = ENTRIES[idx];
    if (!e) { card.innerHTML = "<p class='dim'>No matches stored for this league.</p>"; return; }
    if (e.kind === 'r') return renderResult(e);
    const f = e;
    const when = shortDate(f.date) + (f.time ? ' \\u00b7 ' + f.time : '') +
      (f.round ? ' \\u00b7 Round ' + f.round : '');
    const pair = (title, left, right) =>
      "<h4 class='fx-h'>" + title + '</h4>' +
      "<div class='fx-cols'><div>" + left + '</div><div>' + right + '</div></div>';
    const heads = "<div class='fx-cols fx-names'><div>" + esc(f.home) +
      " <span class='dim'>(home)</span></div><div>" + esc(f.away) +
      " <span class='dim'>(away)</span></div></div>";
    // the two clubs can be anchored to different seasons in August, so the
    // venue heading names the season(s) rather than claiming "this season"
    const vs = [f.home, f.away]
      .map((n) => ((D.venue || {})[n] || {}).season)
      .filter(Boolean);
    const vlabel = 'By venue' + (vs.length
      ? ' \\u2014 ' + esc(vs[0] === vs[1] ? vs[0] : vs.join(' / ')) : '');
    card.innerHTML =
      "<div class='fx-head'><h4>" + esc(f.home) + " <span class='dim'>v</span> " +
        esc(f.away) + "</h4><span class='dim'>" + esc(when) + '</span></div>' +
      verdict(f) +
      heads +
      pair('Recent form \\u2014 last six, newest first', formStrip(f.home), formStrip(f.away)) +
      pair(vlabel, venueBox(f.home, 'h'), venueBox(f.away, 'a')) +
      "<h4 class='fx-h'>Head to head</h4>" + h2hBlock(f) +
      pair('Leading attackers', playerBox(f.home), playerBox(f.away));
  }

  function rebuild() {
    D = FIXTURES_BY_LG[window.CUR_LG] || null;
    const results = (D && D.results) || [];
    const fixtures = (D && D.fixtures) || [];
    ENTRIES = results.concat(fixtures);
    // open on the next fixture rather than an old result: the upcoming match
    // is what someone arriving at this tab is usually after
    idx = fixtures.length ? results.length : 0;
    if (wanted) {
      const i = ENTRIES.findIndex((e) => String(e.id) === wanted);
      if (i >= 0) idx = i;
      wanted = null;   // only restores the once; a league switch starts fresh
    }
    const opt = (e, i) => '<option value=' + i + '>' + esc(shortDate(e.date)) +
      ' \\u2014 ' + esc(e.home) + (e.kind === 'r'
        ? ' ' + e.hg + '\\u2013' + e.ag + ' ' : ' v ') + esc(e.away) + '</option>';
    let html = '';
    if (results.length) {
      html += "<optgroup label='Recent results'>" +
        results.map((e, i) => opt(e, i)).join('') + '</optgroup>';
    }
    if (fixtures.length) {
      html += "<optgroup label='Upcoming fixtures'>" +
        fixtures.map((e, i) => opt(e, results.length + i)).join('') + '</optgroup>';
    }
    pick.innerHTML = html;
    pick.disabled = !ENTRIES.length;
    pick.value = idx;
    render();
    // the league switcher syncs the hash before it fires leaguechange, so
    // without this the URL would still name the previous league's match
    remember();
  }
  window.currentMatch = () => (ENTRIES[idx] || {}).id || null;
  const remember = (push) => { if (window.syncHash) window.syncHash(push); };

  // Back/Forward selects without recording another step
  window.selectMatch = function (id) {
    const i = ENTRIES.findIndex((e) => String(e.id) === String(id));
    if (i < 0) return false;
    idx = i;
    pick.value = i;
    render();
    return true;
  };

  function step(by) {
    const list = ENTRIES;
    if (!list.length) return;
    idx = (idx + by + list.length) % list.length;
    pick.value = idx;
    render();
    remember(true);
  }
  pick.addEventListener('change', () => {
    idx = Number(pick.value) || 0;
    render();
    remember(true);
  });
  $('fx-prev').addEventListener('click', () => step(-1));
  $('fx-next').addEventListener('click', () => step(1));

  // ---- opening a match from the League tab ---------------------------
  const entriesOf = (lg) => {
    const d = FIXTURES_BY_LG[lg] || {};
    return (d.results || []).concat(d.fixtures || []);
  };
  const holds = (lg, id) => entriesOf(lg).some((f) => String(f.id) === String(id));

  window.showFixture = function (league, id) {
    if (league && league !== window.CUR_LG) {
      // go through the league switcher's own button so it stays the single
      // place that knows how to change league (it fires leaguechange, which
      // rebuilds this panel) rather than reaching into its state from here
      let moved = false;
      document.querySelectorAll('nav.lgswitch button').forEach((b) => {
        if (b.dataset.lg === league) { b.click(); moved = true; }
      });
      if (!moved && !FIXTURES_BY_LG[league]) return false;
    }
    const i = ENTRIES.findIndex((f) => String(f.id) === String(id));
    if (i < 0) return false;
    idx = i;
    pick.value = i;
    render();
    // panel first would sync the hash before idx moved, writing the match
    // that was open a moment ago
    if (window.showPanel) window.showPanel('fixtures');
    remember(true);
    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    return true;
  };

  window.hasFixture = holds;

  document.addEventListener('leaguechange', rebuild);
  rebuild();
})();

(function () {  // cross-tab links: club names and fixture rows on the League tab
  // One dispatcher for both kinds of link, because they overlap: a club cell
  // sits inside a clickable fixture row, and two independent listeners would
  // race for the same click. Here the more specific target simply wins.
  function lgOf(el) {
    // a row that names its own league wins: the Best of Europe tables rank
    // all five at once, so the panel they sit in cannot answer this
    if (el.dataset && el.dataset.lg) return el.dataset.lg;
    const view = el.closest('.lgview');
    return view ? view.dataset.lg : window.CUR_LG;
  }
  function mark() {
    document.querySelectorAll('[data-team]').forEach((el) => {
      const ok = window.hasTeam && window.hasTeam(lgOf(el), el.dataset.team);
      el.classList.toggle('team-link', !!ok);
      if (ok) {
        el.tabIndex = 0;
        el.setAttribute('role', 'link');
        el.title = 'Open ' + el.dataset.team + ' in Team analytics';
      } else {
        el.removeAttribute('tabindex');
        el.removeAttribute('role');
        el.removeAttribute('title');
      }
    });
    // only rows the explorer actually holds: the League tab lists played
    // matches and can reach past the explorer's slate, and a link that goes
    // nowhere is worse than no link
    document.querySelectorAll('tr[data-fx]').forEach((tr) => {
      const ok = window.hasFixture && window.hasFixture(lgOf(tr), tr.dataset.fx);
      tr.classList.toggle('fx-link', !!ok);
      if (ok) {
        tr.tabIndex = 0;
        tr.setAttribute('role', 'link');
        tr.title = 'Open in the fixture explorer';
      } else {
        tr.removeAttribute('tabindex');
        tr.removeAttribute('role');
      }
    });
    // each hint only appears where its own kind of link really exists
    document.querySelectorAll('.fx-hint').forEach((h) => {
      const sec = h.closest('section.block');
      h.hidden = !(sec && sec.querySelector('tr.fx-link'));
    });
    document.querySelectorAll('.team-hint').forEach((h) => {
      const sec = h.closest('section.block');
      h.hidden = !(sec && sec.querySelector('.team-link'));
    });
  }
  function dispatch(e) {
    if (!e.target.closest) return false;
    const team = e.target.closest('.team-link');
    if (team) return window.showTeam(lgOf(team), team.dataset.team);
    const row = e.target.closest('tr.fx-link');
    if (row) return window.showFixture(lgOf(row), row.dataset.fx);
    return false;
  }
  document.addEventListener('click', dispatch);
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    if (dispatch(e)) e.preventDefault();
  });
  document.addEventListener('leaguechange', mark);
  mark();
})();

(function () {  // browser Back / Forward
  // Every in-page move pushes a history entry, so Back has somewhere to go.
  // Restoring one has to be silent from start to finish: applyLeague makes
  // the panels resettle, and each of them would otherwise sync the hash
  // mid-restore and overwrite the very state being restored.
  function restore() {
    const hash = decodeURIComponent(location.hash.slice(1));
    window.__navRestoring = true;
    try {
      const lg = (/(?:^|&)lg=([^&]+)/.exec(hash) || [])[1];
      if (lg && window.applyLeague) window.applyLeague(lg.replace(/_/g, ' '));
      const panel = hash.split('&').filter((s) => s && !s.includes('='))[0];
      if (panel && window.showPanel) window.showPanel(panel, true);
      const m = (/(?:^|&)m=([^&]+)/.exec(hash) || [])[1];
      if (m && window.selectMatch) window.selectMatch(m);
    } finally {
      window.__navRestoring = false;
    }
  }
  window.addEventListener('popstate', restore);
})();

(function () {  // global search: clubs, players, sections
  const overlay = document.getElementById('gs-overlay');
  if (!overlay) return;
  const input = document.getElementById('gs-input');
  const list = document.getElementById('gs-results');
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  // accents folded, so a query never has to reproduce a name's diacritics:
  // "hakimi" finds Hakimi, "guler" finds the spelling with the umlaut
  const fold = (s) => String(s).toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
  // the folded name plus a map back to the real characters, so the match can
  // be highlighted on the name as it is actually spelled
  function foldMap(label) {
    let n = '';
    const map = [];
    for (let i = 0; i < label.length; i++) {
      const f = fold(label[i]);
      for (let k = 0; k < f.length; k++) { n += f[k]; map.push(i); }
    }
    return { n: n, map: map };
  }
  const GROUPS = [['club', 'Clubs', 6], ['player', 'Players', 8], ['section', 'Sections', 5]];
  let INDEX = null, shown = [], cur = 0;

  function tabName(pid) {
    const b = document.querySelector("nav.tabs button[data-panel='" + pid + "']");
    return b ? b.textContent.trim() : pid;
  }
  // built once, on first open: the page already holds everything this needs,
  // so the index costs nothing to ship and nothing until it is asked for
  function build() {
    const out = [];
    if (typeof TEAMS_BY_LG !== 'undefined') {
      Object.keys(TEAMS_BY_LG).forEach((lg) => {
        TEAMS_BY_LG[lg].forEach((t) => out.push({
          kind: 'club', label: t.team, sub: lg, lg: lg, name: t.team,
          rank: -(t.pts || 0)     // the league leaders first among equal matches
        }));
      });
    }
    if (typeof PLAYERS_BY_LG !== 'undefined') {
      Object.keys(PLAYERS_BY_LG).forEach((lg) => {
        PLAYERS_BY_LG[lg].forEach((p) => out.push({
          kind: 'player', label: p.name, sub: p.team + ' \\u00b7 ' + lg, lg: lg,
          pid: p.id, rank: -(p.min || 0)   // and the players who actually play
        }));
      });
    }
    document.querySelectorAll('section.panel').forEach((panel) => {
      const pid = panel.id.slice(6), tab = tabName(pid);
      out.push({ kind: 'section', label: tab, sub: 'Tab', panel: pid, rank: 0 });
      const seen = {};
      // one entry per heading, not per league: the same block is repeated
      // inside every .lgview, and the click resolves to the visible one
      panel.querySelectorAll('section.block h3').forEach((h) => {
        const title = h.textContent.trim();
        if (!title || seen[title]) return;
        seen[title] = 1;
        out.push({ kind: 'section', label: title, sub: tab, panel: pid,
                   head: title, rank: 1 });
      });
    });
    out.forEach((r) => {
      const f = foldMap(r.label);
      r.n = f.n;
      r.map = f.map;
      r.w = r.n.split(/[^a-z0-9]+/).filter(Boolean);
    });
    return out;
  }

  // exact, then a prefix of the whole name, then of any word in it, then
  // anywhere: "man" puts Manchester City above Emiliano Martinez
  function tokenScore(r, t) {
    if (r.n === t) return 0;
    if (r.n.indexOf(t) === 0) return 1;
    for (let i = 0; i < r.w.length; i++) if (r.w[i].indexOf(t) === 0) return 2;
    return r.n.indexOf(t) >= 0 ? 3 : -1;
  }
  function score(r, toks) {
    let s = 0;
    for (let i = 0; i < toks.length; i++) {
      const t = tokenScore(r, toks[i]);
      if (t < 0) return -1;      // every word of the query has to land
      s += t;
    }
    return s;
  }
  function mark(r, toks) {
    const label = r.label, hits = [];
    toks.forEach((t) => {
      const at = r.n.indexOf(t);
      if (at >= 0) hits.push([r.map[at], r.map[at + t.length - 1]]);
    });
    if (!hits.length) return esc(label);
    let out = '', open = false;
    for (let i = 0; i < label.length; i++) {
      const on = hits.some((h) => i >= h[0] && i <= h[1]);
      if (on && !open) { out += '<mark>'; open = true; }
      if (!on && open) { out += '</mark>'; open = false; }
      out += esc(label[i]);
    }
    return out + (open ? '</mark>' : '');
  }

  function paint() {
    list.querySelectorAll('.gs-item').forEach((el, i) => {
      const on = i === cur;
      el.setAttribute('aria-selected', on ? 'true' : 'false');
      if (on) el.scrollIntoView({ block: 'nearest' });
    });
  }
  function render() {
    const q = fold(input.value.trim());
    const toks = q ? q.split(/\\s+/).filter(Boolean) : [];
    shown = [];
    cur = 0;
    if (!toks.length) {
      list.innerHTML = "<p class='gs-empty'>Clubs, players and the sections of " +
        'this page \\u2014 try a club name, a player, or "justice".</p>';
      return;
    }
    const hits = [];
    // "justice" matches the Insights table and the continental one equally
    // well; break that tie towards the tab the reader is already on
    const tab = document.querySelector("nav.tabs button[aria-selected='true']");
    const here = tab ? tab.dataset.panel : null;
    INDEX.forEach((r) => {
      const s = score(r, toks);
      if (s >= 0) hits.push([s + (r.panel && r.panel !== here ? 0.5 : 0), r]);
    });
    hits.sort((a, b) => a[0] - b[0] || a[1].rank - b[1].rank ||
      (a[1].label < b[1].label ? -1 : 1));
    let html = '';
    GROUPS.forEach((g) => {
      const rows = hits.filter((h) => h[1].kind === g[0]).slice(0, g[2]);
      if (!rows.length) return;
      html += "<div class='gs-group'>" + g[1] + '</div>';
      rows.forEach((h) => {
        const i = shown.length;
        shown.push(h[1]);
        html += "<div class='gs-item' role='option' data-i='" + i +
          "'><span class='gs-name'>" + mark(h[1], toks) + "</span>" +
          "<span class='gs-sub'>" + esc(h[1].sub) + '</span></div>';
      });
    });
    list.innerHTML = html || "<p class='gs-empty'>Nothing here matches \\u201c" +
      esc(input.value.trim()) + '\\u201d.</p>';
    paint();
  }

  function go(r) {
    if (!r) return;
    close();
    if (r.kind === 'club') { window.showTeam(r.lg, r.name); return; }
    // a player's card reads its peer group from the player's own league, so
    // it opens correctly without dragging the whole page to that league
    if (r.kind === 'player') { if (window.showPlayer) window.showPlayer(r.lg, r.pid); return; }
    if (window.showPanel) window.showPanel(r.panel);
    if (!r.head) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
    let target = null;
    document.getElementById('panel-' + r.panel)
      .querySelectorAll('section.block').forEach((s) => {
        const h = s.querySelector('h3'), view = s.closest('.lgview');
        if (!target && h && h.textContent.trim() === r.head && (!view || !view.hidden))
          target = s;
      });
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function openSearch() {
    if (!INDEX) INDEX = build();
    overlay.hidden = false;
    render();
    input.focus();
    input.select();
  }
  function close() { overlay.hidden = true; }

  document.getElementById('gs-open').addEventListener('click', () => openSearch());
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  input.addEventListener('input', render);
  list.addEventListener('click', (e) => {
    const el = e.target.closest('.gs-item');
    if (el) go(shown[Number(el.dataset.i)]);
  });
  list.addEventListener('mousemove', (e) => {
    const el = e.target.closest('.gs-item');
    if (el && Number(el.dataset.i) !== cur) { cur = Number(el.dataset.i); paint(); }
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { cur = Math.min(cur + 1, shown.length - 1); paint(); }
    else if (e.key === 'ArrowUp') { cur = Math.max(cur - 1, 0); paint(); }
    else if (e.key === 'Enter') { go(shown[cur]); }
    else if (e.key === 'Escape') { close(); }
    else return;
    e.preventDefault();
  });
  document.addEventListener('keydown', (e) => {
    if (!overlay.hidden) return;
    const t = e.target, tag = (t.tagName || '').toLowerCase();
    const typing = tag === 'input' || tag === 'select' || tag === 'textarea' ||
      t.isContentEditable;
    if ((e.key === 'k' || e.key === 'K') && (e.ctrlKey || e.metaKey)) openSearch();
    else if (e.key === '/' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) openSearch();
    else return;
    e.preventDefault();
  });

  // the header trigger scrolls away; this one does not
  const fab = document.createElement('button');
  fab.id = 'gs-fab';
  fab.title = 'Search (/)';
  fab.setAttribute('aria-label', 'Search');
  fab.innerHTML = "<svg viewBox='0 0 16 16' width='17' height='17' fill='none' " +
    "stroke='currentColor' stroke-width='1.7' stroke-linecap='round' " +
    "style='vertical-align:-3px'><circle cx='7' cy='7' r='4.5'/>" +
    "<path d='M10.6 10.6 14 14'/></svg>";
  document.body.appendChild(fab);
  fab.addEventListener('click', () => openSearch());
  window.addEventListener('scroll', () => {
    fab.classList.toggle('show', window.scrollY > 500);
  }, { passive: true });
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
    table_season, next_season, next_start = league_season_state(db, league)
    # out of season every backward-looking block gets stamped with the season
    # it belongs to, so none of them can be mistaken for a live table
    # block() escapes the title itself, so these stay raw
    past = f" ({table_season} final)" if next_season else ""
    ahead = f" ({next_season})" if next_season else ""
    return (
        f"<h2>{escape(league)}</h2>"
        + between_seasons_note(table_season, next_season, next_start)
        + standings_table(db, league, past)
        + home_away_table(db, league, past)
        + block("Recent results" + past,
                "<p class='meta fx-hint' hidden>Click a result to see the xG "
                "behind it — and what the model said before kickoff. The "
                "<span class='link-eg'>underlined club names</span> go to that "
                "club's profile instead.</p>"
                + matches_table(db, league, finished=True))
        + block("Upcoming fixtures" + ahead,
                # revealed client-side only where rows really are clickable,
                # so it never promises a link the explorer cannot open
                "<p class='meta fx-hint' hidden>Click a fixture for the full "
                "breakdown — form, head-to-head and the model's call. The "
                "<span class='link-eg'>underlined club names</span> go to that "
                "club's profile instead.</p>"
                + matches_table(db, league, finished=False))
        + predictions_block(db, league)
        + season_projection_block(db, league)
        + season_projection_distribution(db, league)
        + season_projection_simulator(db, league)
        + season_projection_trend(db, league)
        + report_card_block(db, league)
    )


FROZEN_RESULTS = 400   # every result of a finished season on one page; 30
                       # rounds of a 16-club league is 240, so this is headroom


def frozen_league_section(db, league, season):
    """The League tab of a season that is over: how it finished, and every
    result that got it there.

    Deliberately not league_section(). Predictions, the season projection and
    the report card are all statements about a campaign still being played,
    and a frozen page carrying them would print a forecast directly above the
    result it was forecasting.
    """
    past = f" ({season} final)"
    return (
        f"<h2>{escape(league)} <span class='dim'>{escape(str(season))}</span></h2>"
        # no matchday numbers: FotMob's per-match feed has none, and deriving
        # one from each club's match count gets 62 of 77 right when checked
        # against the live 2026 feed -- an R14 that is wrong one time in five
        # is worse than no R14 at all. The ±5R column goes with it, being
        # computed from exactly that number
        + standings_table(db, league, past, trend=False)
        + home_away_table(db, league, past)
        + block(
            f"Every result{past}",
            "<p class='meta team-hint' hidden>Click a club for its style "
            "profile in Team analytics.</p>"
            + matches_table(db, league, finished=True, limit=FROZEN_RESULTS),
            "<p><strong>What it shows.</strong> Every "
            f"{escape(league)} {escape(str(season))} match, newest first, as it "
            "finished. The xG behind each of them is in Team analytics.</p>",
        )
    )


# ------------------------------------------------------------ preseason tab

PRESEASON_BACK_DAYS = 75    # friendlies played within this window count
PRESEASON_AHEAD_DAYS = 45   # ...as do ones scheduled this far ahead


def preseason_available(db):
    """True when friendlies fall inside the display window — outside the
    summer the tab disappears on its own."""
    if not db.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table' AND name='preseason_matches'"
    ).fetchone():
        return False
    lo = (date.today() - timedelta(days=PRESEASON_BACK_DAYS)).isoformat()
    hi = (date.today() + timedelta(days=PRESEASON_AHEAD_DAYS)).isoformat()
    return db.execute(
        "SELECT COUNT(*) FROM main.preseason_matches WHERE match_date BETWEEN ? AND ?",
        (lo, hi),
    ).fetchone()[0] > 0


def preseason_table(db, league, finished, limit=40):
    today = date.today()
    if finished:
        rows = db.execute(
            """SELECT match_date, home_team, home_score, away_score, away_team
               FROM main.preseason_matches
               WHERE league = ? AND finished = 1 AND match_date >= ?
               ORDER BY match_date DESC, match_id LIMIT ?""",
            (league, (today - timedelta(days=PRESEASON_BACK_DAYS)).isoformat(), limit),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT match_date, home_team, home_score, away_score, away_team
               FROM main.preseason_matches
               WHERE league = ? AND finished = 0 AND match_date BETWEEN ? AND ?
               ORDER BY match_date, match_id LIMIT ?""",
            (league, today.isoformat(),
             (today + timedelta(days=PRESEASON_AHEAD_DAYS)).isoformat(), limit),
        ).fetchall()
    if not rows:
        return "<p class='dim'>Nothing in the calendar right now.</p>"
    body = ""
    for match_date, home, hs, as_, away in rows:
        score = (f"<span class='score'>{hs} – {as_}</span>"
                 if hs is not None else "<span class='dim'>vs</span>")
        body += (
            f"<tr><td class='dim'>{escape(match_date or '')}</td>"
            f"<td style='text-align:right'>{escape(home or '')}</td>"
            f"<td style='text-align:center'>{score}</td>"
            f"<td>{escape(away or '')}</td></tr>"
        )
    return f"<div class='card'><table><tbody>{body}</tbody></table></div>"


def preseason_panel(db, leagues):
    caveat = (
        "<div class='caveat'>"
        "<p><strong>Friendlies lie.</strong> Preseason matches are played with rotated "
        "line-ups, experimental shapes and one eye on fitness — the results say little "
        "about the season ahead, and FotMob publishes no xG for them. This tab is for "
        "keeping an eye on what the clubs are up to over the summer, nothing more.</p>"
        "</div>"
    )

    def content(lg):
        return (
            block("Recent friendlies", preseason_table(db, lg, finished=True))
            + block("Upcoming friendlies", preseason_table(db, lg, finished=False))
        )

    views = "".join(lgview(lg, content(lg), i == 0) for i, lg in enumerate(leagues))
    return (
        "<h2>Preseason <span class='dim'>(club friendlies, FotMob)</span></h2>"
        "<p class='meta'>Summer friendlies for the league's clubs — recent scores and "
        "the upcoming schedule. This tab only appears while friendlies are being "
        "played; once the season proper is under way it retires itself.</p>"
        + caveat + views
    )


def coverage_label(db, leagues):
    """Header badge naming the seasons on show — plural, deliberately.

    The big five run autumn to spring and Allsvenskan runs inside a single
    calendar year, so no one season label covers both, and for most of the
    year they genuinely disagree. Folding them into one ("Big five +
    Allsvenskan 2026/27") attaches a season to Allsvenskan that it does not
    have.
    """
    parts = [f"Big five {season_label(db)}".strip()]
    if "Allsvenskan" in leagues and fotmob_available(db):
        season = db.execute(
            "SELECT MAX(season) FROM main.fotmob_team_matches"
        ).fetchone()[0]
        if season:
            parts.append(f"Allsvenskan {season}")
    return " · ".join(parts)


def sources_label(db, leagues):
    """Header suffix: which source covers what, e.g.
    '2025/26, Understat · Allsvenskan 2026, FotMob'.

    Reads the scoped views rather than main., so an archive page names its own
    season instead of the one currently being played, and drops a source
    entirely when this page has no rows from it.
    """
    parts = []
    if season_label(db):
        parts.append(f"{season_label(db)}, Understat")
    fm = [r for r in db.execute(
        "SELECT league, MAX(season) FROM fotmob_team_matches GROUP BY league "
        "ORDER BY league"
    )] if fotmob_available(db) else []
    parts += [f"{lg} {season}, FotMob" for lg, season in fm if lg in leagues]
    return " · ".join(parts)


def understat_available(db):
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'understat%'"
    )}
    if "understat_team_matches" not in tables:
        return False
    return db.execute("SELECT COUNT(*) FROM understat_team_matches").fetchone()[0] > 0


def fotmob_available(db):
    tables = {r[0] for r in db.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table' AND name LIKE 'fotmob%'"
    )}
    if not {"fotmob_team_matches", "fotmob_players"} <= tables:
        return False
    return db.execute("SELECT COUNT(*) FROM main.fotmob_team_matches").fetchone()[0] > 0


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


CHANGELOG_PATH = PROJECT_DIR / "CHANGELOG.md"
CHANGELOG_SHOWN = 4   # dated entries rendered; older ones stay in the file


def _inline_md(text):
    """The little of Markdown the changelog actually uses, escaped first so a
    stray angle bracket in an entry can never become markup."""
    out = escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![*\w])\*([^*]+?)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"`([^`]+?)`", r"<code>\1</code>", out)
    return out


CHANGELOG_COMMITS = 400   # how far back to read trailers


def parse_changelog(path=CHANGELOG_PATH):
    """[(heading, [bullet, ...]), ...] newest first, from the file only."""
    if not path.exists():
        return []
    entries, current = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.rstrip()
        if line.startswith("## "):
            current = (line[3:].strip(), [])
            entries.append(current)
        elif line.startswith("- ") and current is not None:
            current[1].append(line[2:].strip())
    return [e for e in entries if e[1]]


def git_changelog(limit=CHANGELOG_COMMITS):
    """Entries from `Changelog:` trailers in commit messages, newest first.

    A commit opts in by ending its message with a line like

        Changelog: **Match reports.** What happened, and what we predicted.

    which keeps the site's news out of the commit subjects — those are
    written for whoever edits this code next, and read badly as release
    notes. Nightly data commits carry no trailer and so never appear.

    Returns [] rather than raising when git is unavailable or the checkout
    is too shallow to have the history, so a build from a tarball still
    works off the file alone.
    """
    fmt = "%ad\t%(trailers:key=Changelog,valueonly,separator=%x1f)"
    try:
        out = subprocess.run(
            ["git", "log", f"-{limit}", "--date=short", f"--pretty=format:{fmt}"],
            cwd=PROJECT_DIR, capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    by_date = {}
    order = []
    for line in out.stdout.splitlines():
        if "\t" not in line:
            continue          # a trailer that wrapped onto its own line
        day, _, rest = line.partition("\t")
        texts = [t.strip() for t in rest.split("\x1f") if t.strip()]
        if not texts:
            continue
        if day not in by_date:
            by_date[day] = []
            order.append(day)
        by_date[day].extend(texts)
    return [(day, by_date[day]) for day in order]


def changelog_entries():
    """Commit trailers, with CHANGELOG.md overriding whole dates.

    A date present in the file replaces anything the commits said for that
    day, which is what makes an entry adjustable after the fact: a trailer
    is frozen in history and cannot be edited, so the file is the place to
    correct, reword or drop one. Dates only in the file are added as-is,
    so it also works standalone for anything no commit announced.
    """
    manual = parse_changelog()
    manual_by_date = {heading: bullets for heading, bullets in manual}
    merged = list(manual)
    for day, texts in git_changelog():
        if day not in manual_by_date:
            merged.append((day, texts))
    merged.sort(key=lambda e: e[0], reverse=True)
    return merged


def changelog_block(entries):
    if not entries:
        return ""
    body = ""
    for heading, bullets in entries[:CHANGELOG_SHOWN]:
        body += (f"<h4 class='cl-date'>{escape(heading)}</h4><ul class='cl-list'>"
                 + "".join(f"<li>{_inline_md(b)}</li>" for b in bullets)
                 + "</ul>")
    more = ""
    if len(entries) > CHANGELOG_SHOWN:
        more = (f"<p class='meta'>{len(entries) - CHANGELOG_SHOWN} older "
                "entries are in CHANGELOG.md in the repository.</p>")
    return ("<details class='about changelog'><summary>What's new"
            f" <span class='dim'>· {escape(entries[0][0])}</span></summary>"
            f"<div class='about-body'>{body}{more}</div></details>")


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


def teams_panel(db, leagues, archive=False):
    tables = "".join(
        lgview(lg, xg_table(db, lg), i == 0) for i, lg in enumerate(leagues)
    )
    charts = "".join(
        lgview(lg, style_scatter(db, lg) + rolling_sparklines(db, lg), i == 0)
        for i, lg in enumerate(leagues)
    )
    teams_by_lg = {lg: load_teams(db, lg) for lg in leagues}
    return (
        f"<h2>Team analytics <span class='dim'>({sources_label(db, leagues)})</span></h2>"
        + metric_glossary() + tables
        # recent form is deliberately cross-season, which is right on the live
        # dashboard and wrong on a frozen one: an archive page would show a
        # club's 2026 results under a 2018/19 heading, and every archive file
        # would churn each time anybody played
        + team_compare(teams_by_lg,
                       {lg: load_team_matches(db, lg) for lg in leagues},
                       {} if archive else {lg: load_team_form(db, lg) for lg in leagues},
                       # club history is cross-season too, but capped at the
                       # season each page is about, so an archive page shows
                       # the run-up to its own season and nothing after it
                       {lg: load_club_history(db, lg, [t["team"] for t in teams_by_lg[lg]])
                        for lg in leagues},
                       # only the leagues the explorer cannot reach; the rest
                       # is filtered out of the player list already on the page
                       {lg: load_squads(db, lg, [t["team"] for t in teams_by_lg[lg]])
                        for lg in leagues})
        + charts
    )


def fotmob_attackers_table(db, league, limit=25, min_minutes=450):
    rows = db.execute(
        """SELECT player_name, team, minutes, goals, xg, assists, xa,
                  shots, shots_on_target, chances_created,
                  (xg + xa) * 90.0 / minutes AS threat
           FROM fotmob_players WHERE league = ? AND minutes >= ?
           ORDER BY threat DESC, player_name LIMIT ?""",
        (league, min_minutes, limit),
    ).fetchall()
    if not rows:
        return ""
    body = ""
    for i, (name, team, minutes, goals, xg, assists, xa, shots, sot, cc, threat) in enumerate(rows, 1):
        body += (
            f"<tr><td class='num'>{i}</td><td>{escape(name)}</td>"
            f"<td class='dim'>{escape(team)}</td><td class='num'>{minutes}</td>"
            f"<td class='num'>{goals}</td><td class='num'>{xg:.1f}</td>"
            f"<td class='num'>{assists}</td><td class='num'>{xa:.1f}</td>"
            f"<td class='num'>{shots if shots is not None else '–'}</td>"
            f"<td class='num'>{sot if sot is not None else '–'}</td>"
            f"<td class='num'>{cc}</td><td class='num score'>{threat:.2f}</td></tr>"
        )
    table = (
        "<div class='card'><table><thead><tr>"
        "<th class='num'>#</th><th>Player</th><th>Team</th><th class='num'>Min</th>"
        "<th class='num'>G</th><th class='num' title='expected goals'>xG</th>"
        "<th class='num'>A</th><th class='num' title='expected assists'>xA</th>"
        "<th class='num'>Shots</th><th class='num' title='shots on target'>SoT</th>"
        "<th class='num' title='chances created'>CC</th>"
        "<th class='num' title='xG + xA per 90 minutes'>xG+xA/90</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )
    about = (
        "<p><strong>What it shows.</strong> The league's most dangerous attackers "
        "(450+ minutes), ranked by expected goals plus expected assists per 90 "
        "minutes — chance quality created for themselves and for teammates.</p>"
        "<p><strong>Where the numbers come from.</strong> Allsvenskan has no free "
        "Understat-style feed, so these stats come from FotMob (Opta data): xG, xA, "
        "shots, shots on target and chances created. xGChain, xGBuildup and "
        "per-player non-penalty xG aren't published there, which is why this league "
        "has curated boards instead of the full player explorer.</p>"
    )
    return block("Most dangerous attackers — xG + xA per 90", table, about)


def fotmob_finishing_rows(db, league, order, limit=10, min_minutes=600):
    # 600 minutes rather than 900: Allsvenskan plays 30 rounds, not 38
    return db.execute(
        f"""SELECT player_name, team, minutes, shots, goals, xg, goals - xg AS diff
            FROM fotmob_players WHERE league = ? AND minutes >= ?
            ORDER BY diff {order}, player_name LIMIT ?""",
        (league, min_minutes, limit),
    ).fetchall()


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
        if lg not in UNDERSTAT_LEAGUES:
            # FotMob-backed league: curated boards instead of the explorer
            if not fotmob_available(db):
                return ""
            return (
                fotmob_attackers_table(db, lg)
                + "<div class='duo'>"
                + block("Clinical finishers — most goals above xG",
                        player_table(fotmob_finishing_rows(db, lg, "DESC"), "G−xG"),
                        finishing_about)
                + block("Wasteful in front of goal — most goals below xG",
                        player_table(fotmob_finishing_rows(db, lg, "ASC"), "G−xG"),
                        wasteful_about)
                + "</div>"
            )
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
        f"<h2>Players <span class='dim'>({sources_label(db, leagues)})</span></h2>"
        + metric_glossary()
        + player_explorer(players_by_lg, load_player_careers(db, players_by_lg))
        + player_compare()
        + views
    )


def scope_to_current_season(db):
    # the fetchers keep prior seasons in the database (season is part of each
    # primary key), and no query in this file filters on it: these temp views
    # shadow the real tables so the report never mixes seasons. Per-league
    # rather than global, because calendar-year leagues (Allsvenskan) label
    # seasons differently from the autumn-spring big five.
    #
    # matches/standings anchor on the newest season with a COMPLETED match, not
    # MAX(season): TheSportsDB publishes next season's fixtures early, and a
    # plain MAX would scope a league to a handful of unplayed games all summer.
    # Seasons newer than the anchor stay visible (upcoming fixtures), which is
    # safe because only the anchor season can contain completed matches.
    played = (
        "(SELECT MAX(u.season) FROM main.matches u "
        "WHERE u.league = t.league AND u.home_score IS NOT NULL)"
    )
    db.execute(
        "CREATE TEMP VIEW matches AS SELECT * FROM main.matches t "
        f"WHERE t.season >= {played}"
    )
    db.execute(
        "CREATE TEMP VIEW standings AS SELECT * FROM main.standings t "
        f"WHERE t.season >= {played}"
    )
    # Understat only serves played matches, so newest-with-rows is safe here
    db.execute(
        "CREATE TEMP VIEW understat_players AS "
        "SELECT * FROM main.understat_players t "
        "WHERE t.season = (SELECT MAX(u.season) FROM main.understat_players u "
        "WHERE u.league = t.league)"
    )
    # Allsvenskan's per-match xG comes from FotMob, projected into the
    # Understat shape (PPDA/deep don't exist there and stay NULL) so the
    # xG table, form curves, head-to-head and npxGD insights just work.
    # Databases that never ran fetch_fotmob.py simply skip the union.
    understat_current = (
        "SELECT season, league, team, match_date, home_away, xg, xga, npxg, npxga, "
        "       ppda, ppda_allowed, deep, deep_allowed, scored, missed, xpts, "
        "       result, pts, npxgd, fetched_at "
        "FROM main.understat_team_matches t "
        "WHERE t.season = (SELECT MAX(u.season) FROM main.understat_team_matches u "
        "WHERE u.league = t.league)"
    )
    if fotmob_available(db):
        db.execute(
            "CREATE TEMP VIEW understat_team_matches AS " + understat_current +
            " UNION ALL "
            "SELECT season, league, team, match_date, home_away, xg, xga, npxg, npxga, "
            "       NULL, NULL, NULL, NULL, scored, missed, xpts, result, pts, npxgd, fetched_at "
            "FROM main.fotmob_team_matches f "
            "WHERE f.season = (SELECT MAX(u.season) FROM main.fotmob_team_matches u "
            "WHERE u.league = f.league)"
        )
        for table in ("fotmob_players", "fotmob_team_matches"):
            db.execute(
                f"CREATE TEMP VIEW {table} AS SELECT * FROM main.{table} t "
                f"WHERE t.season = (SELECT MAX(u.season) FROM main.{table} u "
                "WHERE u.league = t.league)"
            )
    else:
        db.execute("CREATE TEMP VIEW understat_team_matches AS " + understat_current)


def scope_to_archive_season(db, season):
    # archive pages are Understat-only: matchday results, standings snapshots
    # and the FotMob-based Allsvenskan data all stay on the live dashboard
    db.execute("CREATE TEMP VIEW matches AS SELECT * FROM main.matches WHERE 0")
    db.execute("CREATE TEMP VIEW standings AS SELECT * FROM main.standings WHERE 0")
    if fotmob_available(db):
        for table in ("fotmob_players", "fotmob_team_matches"):
            db.execute(
                f"CREATE TEMP VIEW {table} AS SELECT * FROM main.{table} WHERE 0"
            )
    for table in ("understat_players", "understat_team_matches"):
        db.execute(
            f"CREATE TEMP VIEW {table} AS SELECT * FROM main.{table} "
            f"WHERE season = '{season}'"
        )


def _sqlq(value):
    """A literal for a CREATE VIEW body. SQLite will not bind parameters into
    a view definition -- the SQL is stored, not executed -- so the season and
    league have to be inlined, and inlining is only safe if it is quoted."""
    return "'" + str(value).replace("'", "''") + "'"


def scope_to_fotmob_season(db, league, season):
    """Freeze the whole report on one finished season of a FotMob-backed
    league.

    scope_to_current_season already teaches the report to read Allsvenskan by
    projecting FotMob's rows into the Understat shape, so every xG block just
    works. This does the same for a season that is over, and supplies the one
    thing the live page gets from TheSportsDB instead -- the match list --
    from those same FotMob rows, which carry both clubs and the scoreline.
    That is why these pages need no new data and no new fetch: everything the
    League tab reads is already stored, just under different column names.
    """
    where = f"league = {_sqlq(league)} AND season = {_sqlq(season)}"
    # TheSportsDB only ever serves the current season, so a finished one has
    # no matches rows at all. round stays NULL because FotMob has no matchday
    # number and guessing one is wrong often enough to notice.
    db.execute(
        "CREATE TEMP VIEW matches AS SELECT "
        "'fm-' || match_id AS event_id, league, season, NULL AS round, "
        "match_date, NULL AS match_time, team AS home_team, "
        "opponent AS away_team, scored AS home_score, missed AS away_score, "
        "'Match Finished' AS status, fetched_at "
        f"FROM main.fotmob_team_matches WHERE {where} AND home_away = 'h'"
    )
    db.execute("CREATE TEMP VIEW standings AS SELECT * FROM main.standings WHERE 0")
    # no Understat rows at all for these leagues, so the tables that feed off
    # understat_players (its explorer, the creators board) empty themselves
    db.execute(
        "CREATE TEMP VIEW understat_players AS "
        "SELECT * FROM main.understat_players WHERE 0"
    )
    db.execute(
        "CREATE TEMP VIEW understat_team_matches AS "
        "SELECT season, league, team, match_date, home_away, xg, xga, npxg, npxga, "
        "       NULL AS ppda, NULL AS ppda_allowed, NULL AS deep, "
        "       NULL AS deep_allowed, scored, missed, xpts, result, pts, npxgd, "
        "       fetched_at "
        f"FROM main.fotmob_team_matches WHERE {where}"
    )
    for table in ("fotmob_players", "fotmob_team_matches"):
        db.execute(f"CREATE TEMP VIEW {table} AS SELECT * FROM main.{table} "
                   f"WHERE {where}")


def fotmob_archive_seasons(db):
    """(league, season) for every FotMob season that is finished.

    "Finished" is simply "not the newest stored": the fetchers only ever add
    to the season being played, so every older one is final and will not
    change again.
    """
    if not fotmob_available(db):
        return []
    return [(r[0], r[1]) for r in db.execute(
        "SELECT league, season FROM main.fotmob_team_matches t "
        "WHERE t.season < (SELECT MAX(u.season) FROM main.fotmob_team_matches u "
        "                  WHERE u.league = t.league) "
        "GROUP BY t.league, t.season ORDER BY t.league, t.season DESC"
    )]


def fotmob_slug(league, season):
    """'Allsvenskan', '2024' -> 'allsvenskan-2024', the archive file stem.

    Kept apart from the big five's '2024-25' stems on purpose: a calendar-year
    league's 2024 is not the same stretch of football as 2024/25, and sharing
    a file would have to pretend one of them is the other.
    """
    stem = re.sub(r"[^a-z0-9]+", "-", league.lower()).strip("-")
    return f"{stem}-{season}"


def season_slug(season):
    """Understat starting year -> archive file stem, '2018' -> '2018-19'."""
    return f"{season}-{(int(season) + 1) % 100:02d}"


def season_nav(db, current=None):
    """Dropdown linking the live dashboard and every archive page.

    `current` is the archive file stem of the page being built, or None on the
    live dashboard itself.

    The big five and a calendar-year league cannot share one flat list without
    misfiling one of them -- Allsvenskan's 2024 is not the big five's 2024/25 --
    so each gets an optgroup, and the season now being played, which is both at
    once, sits above them as the single entry that leads back to the dashboard.
    """
    seasons = [r[0] for r in db.execute(
        "SELECT DISTINCT season FROM main.understat_players ORDER BY season DESC"
    )]
    frozen = fotmob_archive_seasons(db)
    if len(seasons) < 2 and not frozen:
        return ""

    def option(label, slug):
        here = slug == current
        if here:
            href = ""                       # this page
        elif slug is None:
            href = "index.html" if current is None else "../index.html"
        elif current is None:
            href = f"archive/{slug}.html"
        else:
            href = f"{slug}.html"
        return (f"<option value='{escape(href)}'{' selected' if here else ''}>"
                f"{escape(label)}</option>")

    live = (f"{seasons[0]}/{int(seasons[0]) % 100 + 1}" if seasons else "Latest")
    options = [option(f"{live} (current)", None)]
    if len(seasons) > 1:
        options.append("<optgroup label='Big five'>")
        options += [option(f"{s}/{int(s) % 100 + 1}", season_slug(s))
                    for s in seasons[1:]]
        options.append("</optgroup>")
    by_league = {}
    for league, season in frozen:
        by_league.setdefault(league, []).append(season)
    for league, past in by_league.items():
        options.append(f"<optgroup label='{escape(league)}'>")
        options += [option(str(s), fotmob_slug(league, s)) for s in past]
        options.append("</optgroup>")
    return (
        "<nav class='seasonnav'><label for='season-select'>Season</label>"
        "<select id='season-select' "
        "onchange='if(this.value)location.href=this.value+location.hash'>"
        + "".join(options) + "</select></nav>"
    )


STOCKHOLM = "Europe/Stockholm"
NIGHTLY_UTC = (3, 15)   # the cron in .github/workflows/update.yml, in UTC


def _in_stockholm(moment):
    """(wall clock, zone name) for an aware UTC moment.

    The nightly build runs on a GitHub runner, whose clock is UTC, so a bare
    datetime.now() stamped the page two hours behind the only person who
    reads it -- and did it silently, with no zone on the badge to give the
    game away. Falls back to UTC on a machine with no IANA database rather
    than lying about which hour it is showing.
    """
    try:
        return moment.astimezone(ZoneInfo(STOCKHOLM)), "Stockholm"
    except Exception:
        return moment, "UTC"


def build_stamp(now=None):
    """When this build ran, as '2026-08-25 06:15 (Stockholm)'."""
    moment, zone = _in_stockholm(now or datetime.now(timezone.utc))
    return f"{moment:%Y-%m-%d %H:%M} ({zone})"


def nightly_start(now=None):
    """The workflow's cron as a Stockholm wall clock, derived rather than
    written down so it stays right either side of the daylight-saving
    switch instead of being an hour out for half the year."""
    day = (now or datetime.now(timezone.utc)).date()
    fires = datetime(day.year, day.month, day.day, *NIGHTLY_UTC,
                     tzinfo=timezone.utc)
    moment, zone = _in_stockholm(fires)
    return f"{moment:%H:%M} " + ("Stockholm time" if zone == "Stockholm" else "UTC")


SEARCH_ICON = (
    "<svg viewBox='0 0 16 16' width='14' height='14' aria-hidden='true' fill='none' "
    "stroke='currentColor' stroke-width='1.7' stroke-linecap='round'>"
    "<circle cx='7' cy='7' r='4.5'/><path d='M10.6 10.6 14 14'/></svg>"
)

# the trigger sits under the badges where the eye already is; the palette it
# opens is reachable from anywhere by "/" or Ctrl-K, and by the floating button
# that appears once the header has scrolled away
SEARCH_BAR = (
    "<div class='gs-bar'><button id='gs-open' type='button'>" + SEARCH_ICON +
    "<span class='gs-ph'>Search clubs, players and sections</span><kbd>/</kbd>"
    "</button></div>"
)

SEARCH_HTML = (
    "<div id='gs-overlay' hidden><div id='gs-modal' role='dialog' aria-modal='true' "
    "aria-label='Search'>"
    "<input id='gs-input' type='search' autocomplete='off' spellcheck='false' "
    "enterkeyhint='go' placeholder='Search clubs, players and sections' "
    "aria-label='Search'>"
    "<div id='gs-results'></div>"
    "<p class='gs-foot'>↑↓ to move · Enter to open "
    "· Esc to close</p></div></div>"
)


def build_page(db, nav, generated, archive_label=None, frozen=None):
    """The full dashboard HTML. archive_label (e.g. '2018/19') switches to the
    archive layout: no volatile 'Updated' badge, so the file only changes when
    the code or data does.

    frozen is (league, season) for a finished season that still has its full
    results -- a FotMob-backed league, whose per-match feed keeps every
    scoreline. Those pages get a League tab; the Understat archives cannot,
    because Understat has no match feed behind it to rebuild one from."""
    archive = archive_label is not None
    # a frozen FotMob season names its leagues from its own synthesised match
    # list, the Understat archives from the only table they have
    league_table = "understat_players" if archive and not frozen else "matches"
    stored = [
        r[0] for r in db.execute(f"SELECT DISTINCT league FROM {league_table} ORDER BY league")
        if r[0] not in HIDDEN_LEAGUES
    ]
    leagues = [lg for lg in LEAGUE_ORDER if lg in stored]
    leagues += [lg for lg in stored if lg not in leagues]

    panels = []
    if frozen:
        panels.append(("league", "League", "".join(
            lgview(lg, frozen_league_section(db, lg, frozen[1]), i == 0)
            for i, lg in enumerate(leagues)
        )))
    elif not archive:
        panels.append(("league", "League", "".join(
            lgview(lg, league_section(db, lg), i == 0) for i, lg in enumerate(leagues)
        )))
        fixtures = fixtures_panel(db, leagues)
        if fixtures:
            panels.append(("fixtures", "Matches", fixtures))
        if preseason_available(db):
            panels.append(("preseason", "Preseason", preseason_panel(db, leagues)))
    if understat_available(db):
        panels.append(("teams", "Team analytics", teams_panel(db, leagues, archive)))
        panels.append(("players", "Players", players_panel(db, leagues)))
        panels.append(("insights", "Insights", insights_panel(db, leagues)))
        if len(leagues) > 1:
            panels.append(("europe", "Best of Europe", europe_panel(db)))

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

    if frozen:
        league, season = frozen
        badge_texts = [f"{league} {season}", "Season archive", "FotMob"]
        title = f"Football dashboard — {league} {season}"
        tagline = (f"{league} {season}, complete — the final table, every result "
                   "of the season, and the xG behind them.")
        footer = (f"Season archive — {league} {season} exactly as it finished, "
                  "computed from FotMob's per-match data rather than copied "
                  "from a table. Rebuilt whenever the report generator changes.")
    elif archive:
        badge_texts = [f"Big five leagues {archive_label}", "Season archive", "Understat"]
        title = f"Football dashboard — {archive_label}"
        tagline = (
            f"The {archive_label} season in the rear-view mirror — xG team analytics, "
            "player profiles and second-order insights, frozen at full time. "
            "Matchday results and standings live on the current dashboard only."
        )
        footer = ("Season archive — final Understat data for a finished campaign. "
                  "Rebuilt whenever the report generator changes.")
    else:
        badge_texts = [coverage_label(db, leagues),
                       "TheSportsDB + Understat + FotMob", f"Updated {generated}"]
        title = "Football dashboard"
        tagline = ("The big five European leagues — plus Allsvenskan — under the hood: "
                   "standings, xG team analytics, player profiles and second-order "
                   "insights.")
        # the old text here told the reader to run the fetchers by hand, which
        # has not been true since the nightly workflow existed. What is
        # actually worth knowing is when to come back for last night's results
        # -- but only as a habit, not a promise: GitHub's scheduler is
        # best-effort and has started this run eleven hours late, so the
        # sentence has to send the reader to the badge rather than the clock
        footer = ("Standings are computed from the stored results. The site "
                  f"rebuilds itself overnight, usually from about {nightly_start()}, "
                  "and takes around a quarter of an hour — though the schedule is "
                  "best-effort and can run hours late, so the <em>Updated</em> badge "
                  "at the top is the one to trust for how fresh this page is.")
    badges = "".join(f"<span class='badge'>{escape(t)}</span>" for t in badge_texts)
    # the archive pages are frozen snapshots of a finished season; a running
    # list of what changed this week belongs on the live dashboard only
    whats_new = "" if archive else changelog_block(changelog_entries())

    return (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{CSS}</style></head><body><div class='wrap'>"
        f"<header class='hero'><h1>Football dashboard</h1>"
        f"<p class='tagline'>{tagline}</p>"
        f"<div class='badges'>{badges}</div>{SEARCH_BAR}{whats_new}</header>"
        + nav + lg_bar + tab_bar + panel_html
        + f"<footer>{footer}</footer></div>{SEARCH_HTML}"
        f"<script>{EXPLORER_JS}{_poisson_js()}</script></body></html>"
    )


STALE_HOURS = 18   # a nightly build fetches immediately before building, so
                   # its data is minutes old. This only fires on a rebuild
                   # that skipped the fetch, which is exactly the case worth
                   # shouting about

# every source that stamps its rows with a fetch time; standings carry
# snapshot_date instead and move only when the table itself does
FRESHNESS_TABLES = [
    ("results and fixtures", "matches"),
    ("Understat xG", "understat_team_matches"),
    ("FotMob xG", "fotmob_team_matches"),
    ("preseason friendlies", "preseason_matches"),
]


def stale_sources(db, now=None):
    """(label, timestamp, age in hours) for each source older than STALE_HOURS.

    Covers a gap --strict cannot: --strict aborts when a fetcher *fails*, but
    nothing notices when a fetcher simply was not run. A rebuild from a
    day-old database then republishes day-old numbers over fresher ones that
    are already live, and every other check still passes — the build
    succeeds, the safety greps pass, the page renders, and the 'Generated'
    badge says today, because it records when the HTML was built and not how
    old the data inside it is. That is precisely what happened on 2026-08-23:
    a rebuild from a stale local copy erased Serie A's opening results from
    the published page.

    Checked per table rather than per league on purpose. A league that
    returns no rows (Serie A had no Understat data at all for weeks into the
    2026/27 season) never updates its own fetched_at, so per-league checks
    would cry stale every night for a league that is simply not covered yet.
    """
    now = now or datetime.now(timezone.utc)
    out = []
    for label, table in FRESHNESS_TABLES:
        try:
            raw = db.execute(f"SELECT MAX(fetched_at) FROM main.{table}").fetchone()[0]
        except sqlite3.Error:
            continue      # table absent from this database entirely
        if not raw:
            continue      # never fetched: nothing to have gone stale
        try:
            stamp = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age = (now - stamp).total_seconds() / 3600
        if age > STALE_HOURS:
            out.append((label, raw, age))
    return out


def warn_if_stale(db):
    stale = stale_sources(db)
    if not stale:
        return
    print(f"  ! stale data - these sources have not been fetched for over "
          f"{STALE_HOURS}h:")
    for label, raw, age in stale:
        print(f"      {label}: last fetched {raw} ({age:.0f}h ago)")
    print("    Publishing this build would overwrite the live page with data "
          "older than it. Run `python update.py` first.")


def show_changelog() -> None:
    """`python build_report.py --changelog` — what the What's new panel will
    say, and where each date came from, so an entry can be corrected in
    CHANGELOG.md before it is published."""
    manual = {h for h, _ in parse_changelog()}
    entries = changelog_entries()
    if not entries:
        print("No changelog entries: no CHANGELOG.md dates and no Changelog: "
              "trailers found in the last "
              f"{CHANGELOG_COMMITS} commits.")
        return
    for heading, bullets in entries:
        src = "CHANGELOG.md" if heading in manual else "commit trailers"
        star = " (shown)" if entries.index((heading, bullets)) < CHANGELOG_SHOWN else ""
        print(f"\n## {heading}   [{src}]{star}")
        for b in bullets:
            print(f"  - {b}")
    print(f"\n{len(entries)} dated entries, newest {CHANGELOG_SHOWN} rendered on "
          "the page.\nTo change a date the commits produced, add that same date "
          "to CHANGELOG.md:\nthe file replaces whatever the trailers said for "
          "that day.")


def main() -> None:
    if "--changelog" in sys.argv:
        return show_changelog()
    if not DB_PATH.exists():
        raise SystemExit("No football.sqlite found - run `python fetch_data.py` first.")
    generated = build_stamp()

    db = sqlite3.connect(DB_PATH)
    warn_if_stale(db)
    scope_to_current_season(db)
    html = build_page(db, season_nav(db), generated)
    REPORT_PATH.write_text(html, encoding="utf-8")
    DOCS_PATH.parent.mkdir(exist_ok=True)
    DOCS_PATH.write_text(html, encoding="utf-8")
    archive_seasons = [r[0] for r in db.execute(
        "SELECT DISTINCT season FROM main.understat_players "
        "WHERE season < (SELECT MAX(season) FROM main.understat_players) "
        "ORDER BY season DESC"
    )]
    frozen_seasons = fotmob_archive_seasons(db)
    db.close()
    print(f"Report written to {REPORT_PATH}")
    print(f"Dashboard copy written to {DOCS_PATH} (commit it and it's served by GitHub Pages)")

    # one frozen page per past season, next to the report and under docs/
    # (the local copy keeps report.html's season dropdown working offline)
    local_dir = PROJECT_DIR / "archive"
    docs_dir = DOCS_PATH.parent / "archive"
    for target in (local_dir, docs_dir):
        target.mkdir(exist_ok=True)
    for season in archive_seasons:
        db = sqlite3.connect(DB_PATH)
        scope_to_archive_season(db, season)
        label = f"{season}/{int(season) % 100 + 1}"
        html = build_page(db, season_nav(db, season_slug(season)), generated,
                          archive_label=label)
        for target in (local_dir, docs_dir):
            (target / f"{season_slug(season)}.html").write_text(html, encoding="utf-8")
        db.close()
    if archive_seasons:
        print(f"Archive pages written for {len(archive_seasons)} seasons "
              f"({season_slug(archive_seasons[-1])} … {season_slug(archive_seasons[0])}) "
              f"to {docs_dir} and {local_dir}")

    # ...and one per finished season of a FotMob-backed league, which unlike
    # the Understat archives can carry its own results and final table
    for league, season in frozen_seasons:
        db = sqlite3.connect(DB_PATH)
        scope_to_fotmob_season(db, league, season)
        slug = fotmob_slug(league, season)
        html = build_page(db, season_nav(db, slug), generated,
                          archive_label=f"{league} {season}",
                          frozen=(league, season))
        for target in (local_dir, docs_dir):
            (target / f"{slug}.html").write_text(html, encoding="utf-8")
        db.close()
    if frozen_seasons:
        print(f"Season archives written for {len(frozen_seasons)} finished "
              f"FotMob seasons ({', '.join(f'{lg} {s}' for lg, s in frozen_seasons)})")


if __name__ == "__main__":
    main()
