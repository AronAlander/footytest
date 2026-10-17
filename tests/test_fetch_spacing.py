"""Every request to the fixture feed is spaced, including each league's first.

The feed allows about ten requests a minute and answers an eleventh with a
lockout of two minutes. Pausing before each round but not before the
standings request let a league's table call follow the previous league's
last round with no gap, which tipped the run over the limit once per league.
"""
import fetch_data
from conftest import LEAGUE

SEASON = "2026"


def test_no_two_requests_closer_than_the_pause(db, monkeypatch, tmp_path):
    clock = [0.0]
    stamps = []

    def fetch(url, retries=None):
        stamps.append(clock[0])
        if "lookuptable" in url:
            return {"table": []}
        n = int(url.split("&r=")[1].split("&")[0])
        return {"events": [{"idEvent": f"{url[-12:]}{n}", "strHomeTeam": "A",
                            "strAwayTeam": "B", "strSeason": SEASON}]}

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(fetch_data, "fetch_json", fetch)
    monkeypatch.setattr(fetch_data.time, "sleep", sleep)
    monkeypatch.setattr(fetch_data, "DATA_DIR", tmp_path)
    for lid in ("1", "2"):
        fetch_data.fetch_league(db, LEAGUE, {"season": SEASON, "id": lid, "rounds": 2},
                                "now", full=True)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(stamps) == 6
    assert min(gaps) >= fetch_data.REQUEST_PAUSE - 1e-9
