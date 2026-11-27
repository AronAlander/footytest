"""The big five's current season from FotMob, under Understat's names.

Understat went down on 2026-11-03. FotMob's rows for the big five have to
join the Understat seasons before them by club name, so a club whose name
cannot be placed must stop the run rather than be stored as a second club.
"""
from datetime import date

import pytest

import fetch_fotmob

SEASON = "2026"


def _understat(db, league, teams, season=SEASON):
    for team in teams:
        db.execute(
            "INSERT INTO understat_team_matches (season, league, team, match_date) "
            "VALUES (?,?,?,?)", (season, league, team, "2026-09-01"))


def test_big_five_seasons_flip_on_the_first_of_august():
    assert fetch_fotmob.big_five_season(date(2026, 7, 31)) == "2025"
    assert fetch_fotmob.big_five_season(date(2026, 8, 1)) == "2026"
    lg = fetch_fotmob.big_five("Serie A", date(2026, 11, 27))
    assert (lg.id, lg.season, lg.label) == (55, "2026", "2026/2027")


def test_names_map_onto_understat_including_aliases(db):
    _understat(db, "Bundesliga", ["Bayern Munich", "FC Cologne", "RasenBallsport Leipzig",
                                  "Borussia Dortmund"])
    lg = fetch_fotmob.big_five("Bundesliga", date(2026, 11, 27))
    found = fetch_fotmob.map_names(db, lg, ["Bayern München", "1. FC Köln", "RB Leipzig",
                                            "Borussia Dortmund"])
    assert found == {"Bayern München": "Bayern Munich", "1. FC Köln": "FC Cologne",
                     "RB Leipzig": "RasenBallsport Leipzig",
                     "Borussia Dortmund": "Borussia Dortmund"}


def test_a_club_understat_holds_no_match_for_stops_the_run(db):
    _understat(db, "La Liga", ["Barcelona", "Real Madrid"])
    lg = fetch_fotmob.big_five("La Liga", date(2026, 11, 27))
    with pytest.raises(RuntimeError, match="Atletico Ottawa"):
        fetch_fotmob.map_names(db, lg, ["Barcelona", "Real Madrid", "Atletico Ottawa"])


def test_two_clubs_onto_one_name_stops_the_run(db):
    _understat(db, "La Liga", ["Real Madrid", "Barcelona"])
    lg = fetch_fotmob.big_five("La Liga", date(2026, 11, 27))
    fetch_fotmob.club_names.ALIASES["Real Madrid Castilla"] = "Real Madrid"
    try:
        with pytest.raises(RuntimeError, match="several"):
            fetch_fotmob.map_names(db, lg, ["Real Madrid", "Real Madrid Castilla",
                                            "Barcelona"])
    finally:
        del fetch_fotmob.club_names.ALIASES["Real Madrid Castilla"]


def test_without_this_season_from_understat_a_new_club_keeps_its_name(db):
    """Next August Understat may hold nothing for the new season: clubs
    with history still take its names, a promoted newcomer keeps FotMob's."""
    _understat(db, "Serie A", ["Inter", "AC Milan"], season="2025")
    lg = fetch_fotmob.big_five("Serie A", date(2027, 9, 1))
    found = fetch_fotmob.map_names(db, lg, ["Inter", "AC Milan", "Pisa Nuova"])
    assert found == {"Inter": "Inter", "AC Milan": "AC Milan", "Pisa Nuova": "Pisa Nuova"}


def test_allsvenskan_keeps_fotmob_names(db):
    lg = fetch_fotmob.allsvenskan("2026")
    assert fetch_fotmob.map_names(db, lg, ["Halmstad", "AIK"]) == {
        "Halmstad": "Halmstad", "AIK": "AIK"}


def _fixture(mid, home, away, day):
    return {"id": mid, "home": {"name": home}, "away": {"name": away},
            "status": {"finished": True, "utcTime": f"2026-{day}T15:00:00Z"}}


def _details(mid, home, away, day):
    return {
        "header": {"teams": [{"id": 1, "name": home, "score": 2},
                             {"id": 2, "name": away, "score": 1}]},
        "general": {"matchTimeUTCDate": f"2026-{day}T15:00:00Z"},
        "content": {"stats": {"Periods": {"All": {"stats": [{"stats": [
            {"key": "expected_goals", "stats": ["1.80", "0.60"]},
            {"key": "expected_goals_non_penalty", "stats": ["1.04", "0.60"]},
        ]}]}}}},
    }


def _feed(monkeypatch, tmp_path, fixtures):
    by_id = {str(f["id"]): f for f in fixtures}
    asked = []

    def get_json(url):
        if "/leagues?" in url:
            assert "season=2026%2F2027" in url
            return {"fixtures": {"allMatches": fixtures}, "stats": {}}
        mid = url.split("matchId=")[1]
        asked.append(mid)
        f = by_id[mid]
        return _details(mid, f["home"]["name"], f["away"]["name"],
                        f["status"]["utcTime"][5:10])
    monkeypatch.setattr(fetch_fotmob, "get_json", get_json)
    monkeypatch.setattr(fetch_fotmob.time, "sleep", lambda _: None)
    monkeypatch.setattr(fetch_fotmob, "DATA_DIR", tmp_path)
    return asked


def test_rows_are_stored_under_understat_names_and_season(db, monkeypatch, tmp_path):
    _understat(db, "Bundesliga", ["Bayern Munich", "FC Cologne"])
    _feed(monkeypatch, tmp_path, [_fixture(10, "Bayern München", "1. FC Köln", "09-12")])
    lg = fetch_fotmob.big_five("Bundesliga", date(2026, 11, 27))
    fetch_fotmob.fetch_season(db, lg, "now")
    rows = db.execute("SELECT season, league, team, opponent, home_away, xg, npxg "
                      "FROM fotmob_team_matches ORDER BY home_away DESC").fetchall()
    assert rows == [("2026", "Bundesliga", "Bayern Munich", "FC Cologne", "h", 1.8, 1.04),
                    ("2026", "Bundesliga", "FC Cologne", "Bayern Munich", "a", 0.6, 0.6)]


def test_catch_up_takes_the_oldest_matches_first(db, monkeypatch, tmp_path):
    _understat(db, "Serie A", ["Inter", "Roma"])
    fixtures = [_fixture(100 + n, "Inter", "Roma", f"09-{n + 1:02d}") for n in range(5)]
    asked = _feed(monkeypatch, tmp_path, list(reversed(fixtures)))
    monkeypatch.setattr(fetch_fotmob, "NEW_PER_RUN", 3)
    lg = fetch_fotmob.big_five("Serie A", date(2026, 11, 27))
    fetch_fotmob.fetch_season(db, lg, "now")
    assert asked == ["100", "101", "102"]
    asked.clear()
    fetch_fotmob.fetch_season(db, fetch_fotmob.big_five("Serie A", date(2026, 11, 27)), "now")
    assert asked == ["103", "104"], "the next run picks up where this one stopped"
