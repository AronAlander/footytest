"""One-command site update: fetch fresh data, rebuild the report, publish.

Runs the three fetchers and then build_report.py. A fetcher that fails
(an API being down, say) is reported loudly but does not stop the run --
the report simply rebuilds on the last good data already in the database.
Only a build failure aborts.

Usage:
    python update.py          # fetch + rebuild (report.html, docs/)
    python update.py --push   # ...then commit docs/ and push to GitHub Pages
"""

import subprocess
import sys
import time
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
FETCHERS = ["fetch_data.py", "fetch_understat.py", "fetch_fotmob.py", "fetch_preseason.py"]
SITE_URL = "https://aronalander.github.io/footytest/"


def run_step(script: str) -> bool:
    print(f"\n{'=' * 60}\n>>> {script}\n{'=' * 60}", flush=True)
    result = subprocess.run([sys.executable, str(PROJECT_DIR / script)], cwd=PROJECT_DIR)
    return result.returncode == 0


def git(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=PROJECT_DIR, capture_output=capture, text=True)


def push_site() -> bool:
    print(f"\n{'=' * 60}\n>>> publish to GitHub Pages\n{'=' * 60}", flush=True)
    # commit docs/ BEFORE pulling: the build just rewrote it, and
    # `git pull --rebase` refuses to run over unstaged changes
    if git("status", "--porcelain", "docs", capture=True).stdout.strip():
        git("add", "docs")
        if git("commit", "-m", f"Update data {date.today().isoformat()}").returncode != 0:
            print("! git commit failed")
            return False
    if git("pull", "--rebase").returncode != 0:
        print("! git pull failed -- resolve manually, then `git push` yourself")
        return False
    if git("push").returncode != 0:
        print("! git push failed -- check the network / GitHub credentials and push manually")
        return False
    print(f"pushed -- {SITE_URL} updates in a minute or two (Ctrl+F5 if it looks stale)")
    return True


def main() -> None:
    push = "--push" in sys.argv[1:]
    start = time.time()

    failed = [script for script in FETCHERS if not run_step(script)]
    if failed:
        print(f"\n! fetch failed for: {', '.join(failed)} -- rebuilding on last good data")

    if not run_step("build_report.py"):
        sys.exit("build_report.py failed -- report and site NOT updated")

    if push and not push_site():
        sys.exit(1)

    minutes = (time.time() - start) / 60
    print(f"\nDone in {minutes:.1f} min."
          + ("" if push else " Run with --push to also publish to GitHub Pages."))


if __name__ == "__main__":
    main()
