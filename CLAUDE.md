# Working on this repo

A football dashboard: SQLite database → one generated HTML page. `build_report.py`
turns the database into `report.html` and an identical `docs/index.html`, which
GitHub Pages serves at https://aronalander.github.io/footytest/. `README.md`
describes what the site contains; this file is about how to change it safely.

## Commands

```bash
python update.py            # fetch fresh data, then rebuild everything
python update.py --strict   # abort before the build if any fetcher failed
python build_report.py      # rebuild from the database as it stands
python build_report.py --changelog   # what the What's new panel will show
```

`update.py --push` also commits and pushes. Prefer letting the workflow publish
(see below) over pushing pages by hand.

## Never publish a page built from stale data

The database is gitignored and lives in the Actions cache, so a local copy is
usually days old. Building from it rewrites `docs/` **and** `predictions/log.csv`
— the model's record of what it called before kickoff — with worse data than is
already live. This has actually happened: on 2026-08-23 a rebuild from a stale
local copy erased Serie A's opening results from the published site.

So:

- `build_report.py` prints a stale-data warning when a source is over
  `STALE_HOURS` old. Read it. It is not decoration.
- After building locally to test a code change, `git checkout -- docs predictions`
  before committing. Commit the generator, not its output.
- A code change reaches the site on its own: pushing `build_report.py` triggers
  the workflow, which fetches fresh data, rebuilds every page and commits
  `docs/`. That is the intended path to production.
- Only publish pages by hand from a database you just fetched.

## The nightly workflow

`.github/workflows/update.yml` runs at 03:15 UTC, on manual dispatch, and on any
push touching `update.yml` or `build_report.py`. It restores the database from
the Actions cache (cold cache seeds from `seed/football-seed.sqlite.gz`), runs
`update.py --strict`, greps the built page for the leagues that must be there,
and commits `docs` and `predictions`.

The concurrency group queues runs rather than cancelling them, and `checkout`
pins `ref:` to the branch tip so a queued run does not rebuild a stale tree. If
you touch either, keep both properties — losing them brings back the rebase
conflict that failed the 2026-08-28 nightly.

## Season scoping

`scope_to_current_season` / `scope_to_archive_season` / `scope_to_fotmob_season`
install TEMP VIEWs that shadow `matches`, `standings`, `understat_players`,
`understat_team_matches`, `fotmob_players` and `fotmob_team_matches`. Ordinary
queries see one season and should stay that way.

`fotmob_match_shots` **is** shadowed by all three scoping functions
(`scope_to_current_season`, `scope_to_fotmob_season`, `scope_to_archive_season`),
because the shot profile in Team analytics reads it a whole season at a time.
Its view anchors on `fotmob_team_matches`, not on itself: a match can be
stored before its shotmap is, and a table asked for its own newest season
would answer with the whole previous one. The match reports still ask `main.`
for named match ids, which is unaffected.

`fotmob_match_players` is deliberately **not** shadowed: its only reader is
the live Matches tab, which asks for named match ids and qualifies the table
`main.`. Anything new that reads it unqualified would read every season at
once on a frozen page, so add it to the views first if that day comes.

A query that must read across seasons — a club's history, a player's career —
uses `main.`-qualified table names deliberately, and **caps at the newest season
the scoped view can see**, not at `MAX(season)`. That is what keeps an archive
page showing the run-up to its own season and nothing after it: no hindsight,
and archive pages stay byte-identical between builds.

Any cross-season feature has to be verified on an archive page as well as the
live one, or the cap is untested.

## The generated page

Standard library only, vanilla JavaScript, no build step; the page must work
offline from a double-click. Payloads are packed (`pack_by_league` / `UNPACK_JS`)
— a key list plus value arrays with repeated strings interned, rehydrated
client-side, so consuming code sees plain objects.

`EXPLORER_JS` and `CSS` are plain (non-raw) triple-quoted Python strings. Every
backslash the JavaScript needs must be **doubled** in the source, or Python eats
it before the browser sees it: write `\\u00b7`, `\\s+`.

On this machine Bash heredocs collapse backslashes, so a patch script that has
to match on one must be created with the Write tool, not `cat <<'EOF'`.

## Verifying a change

Claims about the page are checked in a real browser, not by reading the HTML:

```bash
"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new \
  --disable-gpu --virtual-time-budget=6000 --dump-dom "file:///.../harness.html"
```

Inject a small harness before `</body>` that drives the feature and writes its
findings into a `<pre>`, then read that back. Set `PYTHONIOENCODING=utf-8` for
any script that prints club names. Screenshot anything whose *appearance* is the
point — a DOM dump cannot see a broken legend.

Check the archive pages and an Allsvenskan frozen page too. They are built by
the same code from a different scope and are where cross-season bugs surface.

## Before pushing: a fresh agent reviews the diff

Once a change is finished and verified, hand the diff to a subagent with no
context from the session that wrote it, and act on what it finds before pushing.
The author of a change is the worst reader of it: the reasoning that produced
the bug also excuses it on re-reading. A reviewer starting from `git diff` and
this file has none of that history.

Ask it specifically about: whether cross-season queries cap correctly, whether
anything publishes stale data, whether the archive pages still hold, and whether
the change does what the commit message says.

## Commits

Title, then a body that explains why the change exists and what was verified —
past commits are the model. A `Changelog:` trailer becomes an entry in the
site's What's new panel, so write it for a reader of the dashboard, not for a
developer. It is read from git history at build time, which is why the workflow
checks out with full depth.

Write names in full: "Community Patch", not "CP".
