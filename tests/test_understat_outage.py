"""An Understat outage keeps yesterday's xG instead of stopping the site.

On 2026-11-02 Understat could not be reached -- its name would not resolve
from the runner and its server would not answer from anywhere -- and the
strict nightly run refused to publish anything at all, fixtures included.
"""
import sqlite3
import sys
import urllib.error
from datetime import datetime, timezone

import pytest

import build_report
import fetch_understat

SEASON = fetch_understat.SEASON
DOWN = urllib.error.URLError("[Errno -3] Temporary failure in name resolution")


def _db(tmp_path, monkeypatch, stored=True):
    path = tmp_path / "f.sqlite"
    con = sqlite3.connect(path)
    con.executescript(fetch_understat.SCHEMA)
    if stored:
        for league in fetch_understat.LEAGUES:
            con.execute(
                "INSERT INTO understat_team_matches (season, league, team, match_date, "
                "fetched_at) VALUES (?,?,?,?,?)",
                (SEASON, league, "A", "2026-10-31", "2026-11-01T10:30:00+00:00"))
    con.commit()
    con.close()
    monkeypatch.setattr(fetch_understat, "DB_PATH", path)
    monkeypatch.setattr(fetch_understat, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fetch_understat.time, "sleep", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["fetch_understat.py"])
    return path


def _fail_with(monkeypatch, error):
    def fetch(*_args):
        raise error
    monkeypatch.setattr(fetch_understat, "fetch_league", fetch)


def test_unreachable_keeps_the_stored_data(tmp_path, monkeypatch, capsys):
    path = _db(tmp_path, monkeypatch)
    _fail_with(monkeypatch, DOWN)
    fetch_understat.main()                     # no exception: the run goes on
    out = capsys.readouterr().out
    assert "keeping the data stored on 2026-11-01T10:30:00+00:00" in out
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM understat_team_matches").fetchone()[0] == 5


def test_a_server_error_counts_as_unreachable(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    _fail_with(monkeypatch, urllib.error.HTTPError("u", 503, "down", {}, None))
    fetch_understat.main()


def test_nothing_stored_is_still_fatal(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, stored=False)
    _fail_with(monkeypatch, DOWN)
    with pytest.raises(urllib.error.URLError):
        fetch_understat.main()


def test_a_reply_that_does_not_parse_is_still_fatal(tmp_path, monkeypatch):
    """That is the feed changing, not the feed being away."""
    _db(tmp_path, monkeypatch)
    _fail_with(monkeypatch, ValueError("Expecting value: line 1 column 1"))
    with pytest.raises(ValueError):
        fetch_understat.main()


def _stamped(db, when):
    db.execute("INSERT INTO understat_team_matches (season, league, team, match_date, "
               "fetched_at) VALUES ('2026','Serie A','A','2026-10-31',?)", (when,))


def test_the_page_says_how_old_the_xg_is(db):
    _stamped(db, "2026-11-01T10:30:00+00:00")
    now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    badge = build_report.understat_held_badge(db, now)
    assert "Big-five xG as of 1 November" in badge
    assert "Understat unreachable" in badge


def test_no_badge_when_the_xg_is_fresh(db):
    _stamped(db, "2026-11-02T10:30:00+00:00")
    now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    assert build_report.understat_held_badge(db, now) == ""


def test_only_the_live_page_carries_it():
    src = open(build_report.__file__, encoding="utf-8").read()
    assert "    if not archive:\n        badges += understat_held_badge(db)" in src
