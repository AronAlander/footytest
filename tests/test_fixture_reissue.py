"""What happens when the feed re-issues a fixture under a new id.

TheSportsDB does this as housekeeping: Ligue 1 round 23 replaced
"Rennes v Paris Saint-Germain" with the same match at the other venue under
a fresh event id. The round stayed full; only the id changed.

Treating that as a truncated response stopped the nightly build — and
stopped it permanently rather than for one night, because the superseded
row stayed in the table and failed the same check every run after. These
tests pin both halves: a full round survives a re-issue, an empty or short
one still aborts.
"""
import pytest

import fetch_data
from conftest import LEAGUE

SEASON = "2026"


def _feed(monkeypatch, tmp_path, rounds):
    """Serve `rounds` as {round number: [event dicts]}."""
    def fetch(url):
        if "lookuptable" in url:
            return {"table": []}
        n = int(url.split("&r=")[1].split("&")[0])
        return {"events": rounds.get(n, [])}
    monkeypatch.setattr(fetch_data, "fetch_json", fetch)
    monkeypatch.setattr(fetch_data.time, "sleep", lambda _: None)
    monkeypatch.setattr(fetch_data, "DATA_DIR", tmp_path)


def _event(event_id, home="A", away="B", **extra):
    row = {"idEvent": str(event_id), "strHomeTeam": home, "strAwayTeam": away,
           "strSeason": SEASON, "intRound": "1"}
    row.update(extra)
    return row


def _stored(db, event_id, home_score=None, away_score=None, round_number=1):
    db.execute(
        "INSERT INTO matches (event_id, league, season, round, home_team, "
        "away_team, home_score, away_score) VALUES (?,?,?,?,?,?,?,?)",
        (str(event_id), LEAGUE, SEASON, round_number, "A", "B",
         home_score, away_score))
    db.commit()


def test_a_re_issued_fixture_replaces_the_old_one(db, monkeypatch, tmp_path):
    """The real Ligue 1 case: same match, venue swapped, new id."""
    _stored(db, "2489663")
    _feed(monkeypatch, tmp_path,
          {1: [_event("2605949", home="B", away="A")]})
    fetch_data.fetch_league(
        db, LEAGUE, {"season": SEASON, "id": "1", "rounds": 1}, "now")
    ids = {r[0] for r in db.execute("SELECT event_id FROM matches")}
    assert ids == {"2605949"}, "the superseded fixture is forgotten"


def test_the_round_is_not_left_holding_both(db, monkeypatch, tmp_path):
    """A duplicate would give one club an extra fixture to play."""
    _stored(db, "old")
    _feed(monkeypatch, tmp_path, {1: [_event("new")]})
    fetch_data.fetch_league(
        db, LEAGUE, {"season": SEASON, "id": "1", "rounds": 1}, "now")
    assert db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1


def test_a_played_fixture_is_kept_rather_than_deleted(db, monkeypatch, tmp_path):
    """Losing a stored result to a feed hiccup is worse than a duplicate."""
    _stored(db, "played", home_score=2, away_score=1)
    _feed(monkeypatch, tmp_path, {1: [_event("fresh")]})
    fetch_data.fetch_league(
        db, LEAGUE, {"season": SEASON, "id": "1", "rounds": 1}, "now")
    ids = {r[0] for r in db.execute("SELECT event_id FROM matches")}
    assert ids == {"played", "fresh"}


def test_an_empty_round_still_aborts(db, monkeypatch, tmp_path, capsys):
    """A re-issue never empties a round, so empty stays fatal.

    The reason is asserted, not just the abort: downstream guards would stop
    an empty round anyway, so only the message proves the round itself was
    rejected rather than the run failing later for want of any fixtures.
    """
    _stored(db, "42")
    _feed(monkeypatch, tmp_path, {1: []})
    with pytest.raises(RuntimeError, match="incomplete fixture fetch"):
        fetch_data.fetch_league(
            db, LEAGUE, {"season": SEASON, "id": "1", "rounds": 1}, "now")
    assert "no fixtures in response" in capsys.readouterr().out
    assert db.execute("SELECT event_id FROM matches").fetchone()[0] == "42"


def test_a_short_round_still_aborts(db, monkeypatch, tmp_path):
    """Half a round of a 20-club league is truncation, not housekeeping."""
    for n in range(10):
        _stored(db, f"s{n}")
    _feed(monkeypatch, tmp_path,
          {1: [_event(f"r{n}") for n in range(4)]})
    with pytest.raises(RuntimeError, match="incomplete fixture fetch"):
        fetch_data.fetch_league(
            db, LEAGUE, {"season": SEASON, "id": "1", "rounds": 38}, "now")
    assert db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 10
