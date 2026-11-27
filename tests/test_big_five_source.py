"""The big five's current season read from FotMob, everything before from
Understat, and no match ever counted from both.

Both feeds hold August to 3 November 2026, when Understat went down. Every
reader that crosses seasons has to take each season from one source only.
"""
import build_report

LG = "Premier League"
CLUBS = ["Arsenal", "Chelsea", "Everton", "Fulham"]


def _pairings(n):
    """n matches round the four clubs: (home, away) pairs, repeating."""
    pairs = [(a, b) for a in CLUBS for b in CLUBS if a != b]
    return [pairs[i % len(pairs)] for i in range(n)]


def _understat(db, season, n, day0=1):
    for i, (h, a) in enumerate(_pairings(n)):
        day = f"{int(season)}-09-{day0 + i:02d}" if season == "2026" else f"{season}-04-{i + 1:02d}"
        for team, ha, gf, ga, xg, xga in ((h, "h", 2, 1, 1.5, 0.5), (a, "a", 1, 2, 0.5, 1.5)):
            db.execute(
                "INSERT INTO understat_team_matches (season, league, team, match_date, "
                "home_away, xg, xga, npxg, npxga, scored, missed, pts, xpts, npxgd, deep) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (season, LG, team, day, ha, xg, xga, xg, xga, gf, ga,
                 3 if gf > ga else 0, 1.5, xg - xga, 5))


def _fotmob(db, n, season="2026"):
    for i, (h, a) in enumerate(_pairings(n)):
        day = f"2026-09-{1 + i:02d}" if i < 28 else f"2026-10-{i - 27:02d}"
        for team, opp, ha, gf, ga, xg, xga in ((h, a, "h", 2, 1, 1.4, 0.4),
                                               (a, h, "a", 1, 2, 0.4, 1.4)):
            db.execute(
                "INSERT INTO fotmob_team_matches (season, league, match_id, team, "
                "opponent, match_date, home_away, xg, xga, npxg, npxga, scored, "
                "missed, result, pts, npxgd, xpts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (season, LG, f"m{i}", team, opp, day, ha, xg, xga, xg, xga, gf, ga,
                 "w" if gf > ga else "l", 3 if gf > ga else 0, xg - xga, 1.4))


def _played(db, n):
    for i, (h, a) in enumerate(_pairings(n)):
        db.execute(
            "INSERT INTO matches (event_id, league, season, round, match_date, "
            "home_team, away_team, home_score, away_score) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"e{i}", LG, "2026-2027", 1, "2026-09-01", h, a, 2, 1))


def _setup(db, understat_now=6, fotmob_now=10, played=10):
    _understat(db, "2025", 12)
    _understat(db, "2026", understat_now)
    _fotmob(db, fotmob_now)
    _played(db, played)
    db.commit()


def test_fotmob_takes_over_once_caught_up(db):
    _setup(db)
    assert build_report.current_xg_source(db, LG) == "fotmob"
    assert build_report.current_xg_source(db, "Allsvenskan") == "fotmob"


def test_a_half_fetched_season_stays_on_understat(db):
    _setup(db, fotmob_now=5, played=10)
    assert build_report.current_xg_source(db, LG) == "understat"


def test_frozen_understat_rows_count_towards_what_fotmob_must_hold(db):
    """Without a results feed, Understat's own rows say how much was played."""
    _setup(db, understat_now=8, fotmob_now=6, played=0)
    assert build_report.current_xg_source(db, LG) == "understat"


def test_the_switch_can_be_turned_back(db, monkeypatch):
    _setup(db)
    monkeypatch.setattr(build_report, "BIG_FIVE_CURRENT_SOURCE", "understat")
    assert build_report.current_xg_source(db, LG) == "understat"


def test_the_season_view_holds_each_match_once(db):
    _setup(db)
    build_report.scope_to_current_season(db)
    rows = db.execute("SELECT COUNT(*), MIN(xg) FROM understat_team_matches "
                      "WHERE league = ?", (LG,)).fetchone()
    assert rows == (20, 0.4), "ten FotMob matches, two rows each, no Understat"


def test_before_the_switch_the_view_holds_understat_only(db):
    _setup(db, fotmob_now=5)
    build_report.scope_to_current_season(db)
    rows = db.execute("SELECT COUNT(*), MIN(xg) FROM understat_team_matches "
                      "WHERE league = ?", (LG,)).fetchone()
    assert rows == (12, 0.5)
    assert db.execute("SELECT COUNT(*) FROM fotmob_team_matches WHERE league = ?",
                      (LG,)).fetchone()[0] == 0


def test_prediction_strengths_count_each_match_once(db):
    _setup(db)
    strengths, *_ = build_report._team_strengths(db, LG)
    # 12 Understat matches last season and 10 FotMob ones this: each club
    # plays half of them, and the 6 frozen Understat ones this season are not
    # counted a second time
    assert sum(s[2] for s in strengths.values()) == 2 * (12 + 10)


def test_before_the_switch_strengths_read_no_fotmob(db):
    _setup(db, fotmob_now=5)
    strengths, *_ = build_report._team_strengths(db, LG)
    assert sum(s[2] for s in strengths.values()) == 2 * (12 + 6)


def test_club_history_reads_each_season_from_its_source(db):
    _setup(db)
    build_report.scope_to_current_season(db)
    hist = build_report.load_club_history(db, LG, CLUBS)
    assert hist["seasons"] == ["2025/26", "2026/27"]
    played = {s: sum(r[3] for rows in hist["clubs"].values() for r in rows if r[0] == s)
              for s in hist["seasons"]}
    assert played == {"2025/26": 24, "2026/27": 20}


def test_meetings_take_each_season_from_its_source(db):
    _setup(db)
    seasons = [r[0] for r in build_report._resolved_matches(db, LG)]
    assert seasons.count("2025") == 12
    assert seasons.count("2026") == 10, "FotMob's ten, not Understat's six as well"


def test_archive_pages_stop_before_the_fotmob_season(db):
    _setup(db)
    build_report.scope_to_archive_season(db, "2025")
    hist = build_report.load_club_history(db, LG, CLUBS)
    assert hist["seasons"] == ["2025/26"]


def test_the_held_badge_goes_once_every_league_is_current(db):
    from datetime import datetime, timezone
    _setup(db)
    db.execute("UPDATE understat_team_matches SET fetched_at = '2026-11-03T10:00:00+00:00'")
    now = datetime(2026, 11, 27, tzinfo=timezone.utc)
    # the other four leagues have no FotMob rows here, so they still read
    # Understat and the badge names them -- and not the league that moved
    badge = build_report.understat_held_badge(db, now)
    assert "Premier League" not in badge and "Serie A" in badge
    for lg in build_report.UNDERSTAT_LEAGUES:
        if lg != LG:
            db.execute("INSERT INTO fotmob_team_matches (season, league, match_id, team, "
                       "match_date, home_away, xg, xga) VALUES "
                       "('2026', ?, 'x', 'A', '2026-09-01', 'h', 1, 1)", (lg,))
    assert build_report.understat_held_badge(db, now) == ""
