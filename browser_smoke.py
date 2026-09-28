"""Real Chromium smoke test using synthetic data, without network or a live DB.

Run: python browser_smoke.py. Set CHROME_BIN if Chrome is not on PATH.
Outputs live/archive pages, DOM and screenshots under browser-artifacts/.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tests'))
import conftest
from test_fixture_tape import an_understat_league, BIG5
import build_report as b
import projection_log
from unittest.mock import patch


def page():
    db = conftest.db.__wrapped__()
    an_understat_league(db)
    db.execute('UPDATE understat_team_matches SET npxgd=npxg-npxga')
    for n in range(10):
        db.execute('INSERT INTO matches(event_id,league,season,match_date,home_team,away_team,home_score,away_score) '
                   'VALUES(?,?,?,?,?,?,?,?)',
                   (str(n), BIG5, '2026', '2026-04-01', f'Club {n}', f'Club {(n+1)%10}', 1, 0))
        db.execute('INSERT INTO understat_players VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('2026', BIG5, str(n), f'Player {n}', f'Club {n}', 'M', 12, 1080,
                    2, 2.5, 3, 2.1, 10, 8, 2, 2.5, 4.1, 3.2, '2026-04-01'))
        db.execute('INSERT INTO matches(event_id,league,season,match_date,match_time,home_team,away_team) '
                   'VALUES(?,?,?,?,?,?,?)',
                   ('future'+str(n), BIG5, '2026', '2099-04-01', '18:00:00', f'Club {n}', f'Club {(n+1)%10}'))
    # Fixed history for a browser check of the generated trend, independent of today's date.
    snapshots = {}
    for day, pts in [('2026-03-01', 40), ('2026-03-02', 42), ('2026-04-01', 41)]:
        projection_log.record_snapshot(snapshots, day, BIG5, '2026',
                                       [f'Club {n}' for n in range(10)],
                                       [pts]*10, [1]*10, [4]*10, [3]*10, 10)
    with patch.object(b, 'PROJECT_SIMS', 200), patch.object(b, 'changelog_entries', return_value=[]), patch.object(projection_log, 'load', return_value=snapshots):
        live = b.build_page(db, '', 'Synthetic test data')
        archive = b.build_page(db, '', 'Synthetic test data', archive_label='2026/27')
    db.close()
    return live, archive


CHECK = r"""
<script>
setTimeout(() => {
 const check = (ok, message) => { if (!ok) throw Error(message); };
 const visible = id => { const r=document.getElementById(id).getBoundingClientRect(); return r.width>0 && r.height>0; };
 try {
   check(window.smokeErrors.length === 0, window.smokeErrors.join('; '));
   if (location.hash.includes('player=')) {
     check(visible('pd-modal'), 'Cold deep link did not open a visible player');
     check(document.getElementById('pd-modal').textContent.includes('Player 0'), 'Wrong linked player');
     check(window.currentTeams().includes('Club 0'), 'Cold deep link lost its club');
     document.getElementById('pd-close').click();
   }
   document.querySelector("nav.tabs button[data-panel='teams']").click();
   check(!document.getElementById('panel-teams').hidden, 'Teams tab failed');
   window.showTeam('Premier League', 'Club 0');
   check(visible('tc-card'), 'Team card invisible');
   const player = document.querySelector('#tc-card .squad-link');
   check(player, 'Squad player link missing'); player.click();
   check(visible('pd-modal'), 'Player card invisible from team tab');
   check(document.getElementById('pd-modal').textContent.includes('Player 0'), 'Wrong squad card');
   check(location.hash.includes('player='), 'Player deep link missing');
   document.getElementById('pd-close').click();
   document.getElementById('gs-open').click();
   const input=document.getElementById('gs-input'); input.value='Player 0';
   input.dispatchEvent(new Event('input', {bubbles:true}));
   check(document.getElementById('gs-results').textContent.includes('Player 0'), 'Search missing player');
   input.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
   check(document.getElementById('gs-overlay').hidden, 'Search did not close');
   check(window.smokeErrors.length === 0, window.smokeErrors.join('; '));
   document.body.insertAdjacentHTML('beforeend','<pre id="smoke-result">PASS</pre>');
 } catch(e) {
   const pre=document.createElement('pre'); pre.id='smoke-result'; pre.textContent='FAIL: '+e.message;
   document.body.appendChild(pre);
 }
}, 500);
</script>
"""


def main():
    chrome = os.environ.get('CHROME_BIN') or shutil.which('google-chrome') or shutil.which('chromium')
    if not chrome:
        candidate=Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
        chrome=str(candidate) if candidate.exists() else None
    if not chrome:
        raise SystemExit('Chrome required: set CHROME_BIN')
    out=ROOT/'browser-artifacts'; out.mkdir(exist_ok=True)
    live, archive=page()
    for kind, html in [('live',live), ('archive',archive)]:
        html=html.replace('<head>', '<head><script>window.smokeErrors=[]; window.addEventListener("error", e=>window.smokeErrors.push(e.message));</script>',1)
        target=out/f'{kind}.html'; target.write_text(html.replace('</body>', CHECK+'</body>'),encoding='utf-8')
        for width in (1200, 500):
            with tempfile.TemporaryDirectory(prefix='football-browser-') as profile:
                command=[chrome, '--headless=new', '--disable-gpu', '--no-sandbox',
                         '--allow-file-access-from-files', f'--user-data-dir={profile}',
                         f'--window-size={width},1000', '--virtual-time-budget=2500',
                         f'--screenshot={out / (kind+str(width)+".png")}', '--dump-dom',
                         target.as_uri()+'#lg=Premier_League&teams&club=Club%200&player=Player%200']
                result=subprocess.run(command, capture_output=True, encoding='utf-8', timeout=45)
                (out/f'{kind}{width}.dom').write_text(result.stdout,encoding='utf-8')
                match=re.search(r'<pre id="smoke-result">(.*?)</pre>', result.stdout)
                if result.returncode or not match or match[1]!='PASS':
                    raise SystemExit(f'{kind} {width}: '+(match[1] if match else result.stderr[-1500:]))
                print(f'{kind} {width}: PASS')


if __name__ == '__main__':
    main()
