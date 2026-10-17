"""When the fixture feed cannot be reached.

The key is TheSportsDB's public test key, so its rate limit is shared with
everyone else using it, and a 429 partway through the nightly is weather,
not a fault. On 16 and 17 October two of Allsvenskan's thirty rounds were
throttled past their retries and the whole build stopped for two days.

A round that answers wrongly is still fatal. A round that does not answer
at all falls back on what is stored -- unless nothing is stored, or so many
rounds are unreachable that the page would be lying about its freshness.
"""
import urllib.error

import pytest

import fetch_data
from conftest import LEAGUE

SEASON = "2026"


def _throttled():
    return urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)


def _event(n, **extra):
    row = {"idEvent": str(n), "strHomeTeam": "A", "strAwayTeam": "B",
           "strSeason": SEASON}
    row.update(extra)
    return row


def _stored(db, event_id, round_number):
    db.execute(
        "INSERT INTO matches (event_id, league, season, round, home_team, "
        "away_team, home_score, away_score) VALUES (?,?,?,?,?,?,?,?)",
        (str(event_id), LEAGUE, SEASON, round_number, "A", "B", 1, 0))
    db.commit()


def _feed(monkeypatch, tmp_path, answers):
    """answers: {round: [events] | exception}."""
    def fetch(url, retries=None):
        if "lookuptable" in url:
            return {"table": []}
        n = int(url.split("&r=")[1].split("&")[0])
        a = answers.get(n, [])
        if isinstance(a, Exception):
            raise a
        return {"events": a}
    monkeypatch.setattr(fetch_data, "fetch_json", fetch)
    monkeypatch.setattr(fetch_data.time, "sleep", lambda _: None)
    monkeypatch.setattr(fetch_data, "DATA_DIR", tmp_path)


def _league(rounds):
    return {"season": SEASON, "id": "1", "rounds": rounds}


def test_a_throttled_round_keeps_what_is_stored(db, monkeypatch, tmp_path, capsys):
    """The outage of 16 October, written down."""
    _stored(db, "old2", 2)
    _feed(monkeypatch, tmp_path, {1: [_event(1)], 2: _throttled(), 3: [_event(3)]})
    fetch_data.fetch_league(db, LEAGUE, _league(3), "now")
    ids = {r[0] for r in db.execute("SELECT event_id FROM matches")}
    assert ids == {"1", "old2", "3"}, "round 2's stored fixture survives"
    out = capsys.readouterr().out
    assert "round 2: unreachable" in out and "kept from last run" in out


def test_a_round_never_seen_cannot_fall_back(db, monkeypatch, tmp_path):
    _feed(monkeypatch, tmp_path, {1: [_event(1)], 2: _throttled()})
    with pytest.raises(RuntimeError, match="incomplete fixture fetch"):
        fetch_data.fetch_league(db, LEAGUE, _league(2), "now")


def test_too_many_unreachable_rounds_is_an_outage(db, monkeypatch, tmp_path):
    rounds = fetch_data.MAX_UNREACHED + 2
    answers = {1: [_event(1)]}
    for n in range(2, rounds + 1):
        _stored(db, f"old{n}", n)
        answers[n] = _throttled()
    _feed(monkeypatch, tmp_path, answers)
    with pytest.raises(RuntimeError, match="unreachable"):
        fetch_data.fetch_league(db, LEAGUE, _league(rounds), "now")


def test_a_bad_answer_is_still_fatal_even_with_a_fallback(db, monkeypatch, tmp_path):
    """Evidence that is wrong is not the same as no evidence."""
    _stored(db, "old2", 2)
    _feed(monkeypatch, tmp_path, {1: [_event(1)], 2: [{"idEvent": "x"}]})
    with pytest.raises(RuntimeError, match="incomplete fixture fetch"):
        fetch_data.fetch_league(db, LEAGUE, _league(2), "now")


def test_a_bug_in_our_own_checks_is_not_mistaken_for_the_network(db, monkeypatch, tmp_path):
    _stored(db, "old2", 2)
    _feed(monkeypatch, tmp_path, {1: [_event(1)], 2: KeyError("parser bug")})
    with pytest.raises(RuntimeError, match="incomplete fixture fetch"):
        fetch_data.fetch_league(db, LEAGUE, _league(2), "now")


def test_one_wait_outlasts_the_measured_lockout_then_gives_up(monkeypatch):
    """The lockout is a fixed ~120.6 seconds; waiting less cannot end it."""
    waits = []
    monkeypatch.setattr(fetch_data.time, "sleep", waits.append)

    def throttled(*a, **k):
        raise _throttled()
    monkeypatch.setattr(fetch_data.urllib.request, "urlopen", throttled)
    with pytest.raises(urllib.error.HTTPError):
        fetch_data.fetch_json("http://x")
    assert len(waits) == 1 and waits[0] > 121


def test_requests_are_paced_under_the_measured_limit():
    """About ten a minute: 2.1s apart was throttled, 6.2s apart was not."""
    assert fetch_data.REQUEST_PAUSE >= 6.0
