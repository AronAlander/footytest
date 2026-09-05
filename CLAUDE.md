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

`fotmob_match_shots` and `fotmob_match_players` **are** shadowed by all
three scoping functions (`scope_to_current_season`, `scope_to_fotmob_season`,
`scope_to_archive_season`), because the shot profile in Team analytics and the
goalkeeper board in Players each read a whole season of them at a time.
Anything added later that reads a per-match table a whole season at a time
must join the views too, or it will read every season at once on a frozen
page. Both views anchor on `fotmob_team_matches`, not on themselves: a match can be
stored before its shotmap or its lineup is, and a table asked for its own
newest season would answer with the whole previous one. The match reports
still ask `main.` for named match ids, which is unaffected.

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

## Before pushing: `python -m pytest`

The suite in `tests/` is the gate. It must pass before a push, and a change
that adds a block to a page adds its cases to it. The workflow runs it too,
before it fetches anything — a push to `build_report.py` publishes, so the
gate has to sit in front of that and not only on this machine.

It never opens `football.sqlite`. That database lives in the Actions cache,
is not in the repository, and holds whatever the feed sent last night —
so a test that read it would be unrunnable in CI and would check today's
data rather than the rules the code must hold to. Each test instead builds
the real schema out of the fetchers' own `SCHEMA` constants and inserts the
handful of rows it wants, which is the only way to write down "the feed sent
nothing" as a case.

Three failure classes have caused every bug found here so far, and each has
a file:

- `test_missing_data.py` — a figure the feed omitted read as a nought. This
  is the dangerous one, because it publishes a confident wrong answer with
  nothing to see. The rule: a missing figure is either provably a nought or
  it removes the block from the page.
- `test_scoping.py` — a block that reads a season at a time is right on the
  live page and wrong on every archive page unless its table is shadowed,
  and wrong on the first morning of a new season unless its view anchors on
  `fotmob_team_matches` rather than on itself.
- `test_rendering.py` — escaping, byte-identical rebuilds, and arithmetic on
  inputs no season would produce. The nightly is one process: an exception
  in it takes the index page, both archive sets and the prediction log.

A test that passes against the broken code is worse than no test. When you
add one, reintroduce the bug it is meant to catch and watch it fail — every
test in these files has been checked that way.

## Commits

Title, then a body that explains why the change exists and what was verified —
past commits are the model. A `Changelog:` trailer becomes an entry in the
site's What's new panel, so write it for a reader of the dashboard, not for a
developer. It is read from git history at build time, which is why the workflow
checks out with full depth.

Write names in full: "Community Patch", not "CP".
