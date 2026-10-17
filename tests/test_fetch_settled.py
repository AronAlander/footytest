"""Not asking the feed again about rounds that are over.

At ten requests a minute every request costs 6.2 seconds of a run with a
time limit, and most of a season's rounds are finished and will not change.
A round is skipped only if every fixture in it has a result and the last
was played more than a week ago; a weekly full sweep still re-checks them,
which is what catches a late correction to an old result.
"""
from datetime import date

import fetch_data
from conftest import LEAGUE

SEASON = "2026"
TODAY = date(2026, 10, 17)


def _stored(db, event_id, round_number, day, played=True):
    db.execute(
        "INSERT INTO matches (event_id, league, season, round, match_date, "
        "home_team, away_team, home_score, away_score) VALUES (?,?,?,?,?,?,?,?,?)",
        (str(event_id), LEAGUE, SEASON, round_number, day, "A", "B",
         1 if played else None, 0 if played else None))
    db.commit()


def _asked(monkeypatch, tmp_path):
    seen = []

    def fetch(url, retries=None):
        if "lookuptable" in url:
            return {"table": []}
        n = int(url.split("&r=")[1].split("&")[0])
        seen.append(n)
        return {"events": [{"idEvent": f"e{n}", "strHomeTeam": "A",
                            "strAwayTeam": "B", "strSeason": SEASON}]}
    monkeypatch.setattr(fetch_data, "fetch_json", fetch)
    monkeypatch.setattr(fetch_data.time, "sleep", lambda _: None)
    monkeypatch.setattr(fetch_data, "DATA_DIR", tmp_path)
    return seen


def test_a_finished_old_round_is_not_asked_for(db, monkeypatch, tmp_path):
    _stored(db, "old", 1, "2026-09-01")                 # done, six weeks ago
    _stored(db, "e2", 2, "2026-10-14")                  # done, three days ago
    _stored(db, "e3", 3, "2026-09-01", played=False)    # old but unplayed
    seen = _asked(monkeypatch, tmp_path)
    fetch_data.fetch_league(db, LEAGUE, {"season": SEASON, "id": "1", "rounds": 4},
                            "now", full=False, today=TODAY)
    assert seen == [2, 3, 4]


def test_the_skipped_round_keeps_its_stored_fixtures(db, monkeypatch, tmp_path):
    _stored(db, "old", 1, "2026-09-01")
    _asked(monkeypatch, tmp_path)
    fetch_data.fetch_league(db, LEAGUE, {"season": SEASON, "id": "1", "rounds": 2},
                            "now", full=False, today=TODAY)
    ids = {r[0] for r in db.execute("SELECT event_id FROM matches")}
    assert "old" in ids


def test_the_full_sweep_asks_for_everything(db, monkeypatch, tmp_path):
    """The only way a late correction to an old result reaches the site."""
    _stored(db, "old", 1, "2026-09-01")
    seen = _asked(monkeypatch, tmp_path)
    fetch_data.fetch_league(db, LEAGUE, {"season": SEASON, "id": "1", "rounds": 2},
                            "now", full=True, today=TODAY)
    assert seen == [1, 2]


def test_a_season_with_every_round_settled_is_not_an_error(db, monkeypatch, tmp_path):
    """Nothing to fetch is the point, not a failure."""
    _stored(db, "a", 1, "2026-05-01")
    _stored(db, "b", 2, "2026-05-08")
    seen = _asked(monkeypatch, tmp_path)
    fetch_data.fetch_league(db, LEAGUE, {"season": SEASON, "id": "1", "rounds": 2},
                            "now", full=False, today=TODAY)
    assert seen == []


def test_the_week_has_a_full_sweep_in_it():
    assert 0 <= fetch_data.FULL_SWEEP_WEEKDAY <= 6
    assert fetch_data.SETTLED_DAYS >= 7
