"""The model report card: a yardstick on the same matches, and honest coverage.

Both changes exist because the card misled. It quoted one fixed base-rate
Brier score for every league, so a hard run of fixtures read as a bad model;
and it never said the log had begun partway through Allsvenskan's season,
so forty matches from August read as the whole year.
"""
import build_report
import prediction_log
from conftest import LEAGUE


def result(db, event_id, date, home, away, hs, as_, season="2026"):
    db.execute(
        "INSERT INTO matches (event_id, league, season, match_date, home_team, "
        "away_team, home_score, away_score) VALUES (?,?,?,?,?,?,?,?)",
        (str(event_id), LEAGUE, season, date, home, away, hs, as_))


def history(db, n, home_wins, draws, before="2026-08-01"):
    """`n` earlier home results in fotmob_team_matches, ending before `before`."""
    for i in range(n):
        hs, as_ = (1, 0) if i < home_wins else (1, 1) if i < home_wins + draws \
            else (0, 1)
        db.execute(
            "INSERT INTO fotmob_team_matches (season, league, match_id, team, "
            "opponent, match_date, home_away, scored, missed) "
            "VALUES ('2025',?,?,?,?,?,?,?,?)",
            (LEAGUE, f"h{i}", f"Club {i % 8}", f"Club {(i + 1) % 8}",
             f"2025-{1 + i % 12:02d}-{1 + i % 27:02d}", "h", hs, as_))


def call(event_id, date, probs, season="2026"):
    p = [f"{x:.4f}" for x in probs]
    return str(event_id), {
        "event_id": str(event_id), "league": LEAGUE, "season": season,
        "match_date": date, "home": "Home FC", "away": "Away FC",
        "first_seen": date, "p_home_first": p[0], "p_draw_first": p[1],
        "p_away_first": p[2], "last_seen": date, "p_home": p[0],
        "p_draw": p[1], "p_away": p[2], "lam_home": "1.4", "lam_away": "1.1",
    }


def with_log(monkeypatch, calls):
    rows = dict(calls)
    monkeypatch.setattr(prediction_log, "load", lambda *a, **k: rows)


def test_base_rates_only_see_results_before_the_first_graded_match(db):
    """A yardstick that has seen the results it is scored on is not one."""
    history(db, 100, home_wins=50, draws=25)
    # a flood of away wins on and after the cut-off must not count
    for i in range(200):
        db.execute(
            "INSERT INTO fotmob_team_matches (season, league, match_id, team, "
            "match_date, home_away, scored, missed) "
            "VALUES ('2026',?,?,?,?,?,?,?)",
            (LEAGUE, f"late{i}", "Club 0", "2026-08-01", "h", 0, 3))
    base = build_report._report_base_rates(db, LEAGUE, "2026-08-01")
    assert [round(x, 2) for x in base] == [0.50, 0.25, 0.25]


def test_too_little_history_gives_no_yardstick_rather_than_a_noisy_one(db):
    history(db, build_report.REPORT_BASE_MIN - 1, home_wins=20, draws=10)
    assert build_report._report_base_rates(db, LEAGUE, "2026-08-01") is None


def test_the_yardstick_is_scored_on_the_same_matches(db, monkeypatch):
    """Home 50/25/25 forecasting two home wins and two away wins."""
    history(db, 100, home_wins=50, draws=25)
    calls = []
    for i, (hs, as_) in enumerate(((2, 0), (1, 0), (0, 2), (0, 1))):
        result(db, 900 + i, f"2026-08-{10 + i}", "Home FC", "Away FC", hs, as_)
        calls.append(call(900 + i, f"2026-08-{10 + i}", (0.5, 0.25, 0.25)))
    with_log(monkeypatch, calls)
    html = build_report.report_card_block(db, LEAGUE)
    # a home win against 50/25/25 costs 0.375, an away win 0.875
    expected = (2 * 0.375 + 2 * 0.875) / 4
    assert f"{expected:.3f}" in html
    # the base-rate favourite is the home side, right in two of four
    assert html.count("50%") >= 2, "both columns hit half on these matches"


def test_part_of_a_season_says_so(db, monkeypatch):
    history(db, 100, home_wins=50, draws=25)
    for i in range(10):                            # ten played this season
        result(db, 700 + i, f"2026-05-{10 + i}", "Home FC", "Away FC", 1, 0)
    with_log(monkeypatch, [call(709, "2026-05-19", (0.5, 0.3, 0.2))])
    html = build_report.report_card_block(db, LEAGUE)
    assert "1 of the 10 matches" in html
    assert "Part of a season, not a season" in html
    assert "the 9 matches before it were never graded" in html


def test_most_of_a_season_does_not_cry_wolf(db, monkeypatch):
    history(db, 100, home_wins=50, draws=25)
    calls = []
    for i in range(10):
        result(db, 700 + i, f"2026-05-{10 + i}", "Home FC", "Away FC", 1, 0)
        if i:                                      # nine of ten graded
            calls.append(call(700 + i, f"2026-05-{10 + i}", (0.5, 0.3, 0.2)))
    with_log(monkeypatch, calls)
    html = build_report.report_card_block(db, LEAGUE)
    assert "9 of the 10 matches" in html
    assert "Part of a season" not in html


def test_no_history_leaves_the_column_blank_and_still_builds(db, monkeypatch):
    result(db, 800, "2026-08-10", "Home FC", "Away FC", 1, 1)
    with_log(monkeypatch, [call(800, "2026-08-10", (0.4, 0.3, 0.3))])
    html = build_report.report_card_block(db, LEAGUE)
    assert "Home advantage only" in html
    assert "–" in html


def test_the_old_fixed_base_rate_is_gone(db, monkeypatch):
    """0.647 was one league's figure quoted for all six."""
    history(db, 100, home_wins=50, draws=25)
    result(db, 800, "2026-08-10", "Home FC", "Away FC", 1, 1)
    with_log(monkeypatch, [call(800, "2026-08-10", (0.4, 0.3, 0.3))])
    assert "0.647" not in build_report.report_card_block(db, LEAGUE)


def test_a_missed_draw_shows_what_the_call_gave_it(db, monkeypatch):
    """A draw is almost never the call, so the tick can never credit one.

    The share the call gave the draw is what shows whether the model saw it
    coming: 31% was a real possibility even though the top pick was a win.
    """
    history(db, 100, home_wins=50, draws=25)
    result(db, 950, "2026-08-10", "Home FC", "Away FC", 1, 1)
    with_log(monkeypatch, [call(950, "2026-08-10", (0.45, 0.31, 0.24))])
    html = build_report.report_card_block(db, LEAGUE)
    assert "Gave the result" in html
    assert "Draw 31%" in html
    assert "said Home FC 45%" in html


def test_an_away_win_the_call_missed_shows_its_share(db, monkeypatch):
    history(db, 100, home_wins=50, draws=25)
    result(db, 951, "2026-08-11", "Home FC", "Away FC", 0, 2)
    with_log(monkeypatch, [call(951, "2026-08-11", (0.52, 0.27, 0.21))])
    assert "Away FC 21%" in build_report.report_card_block(db, LEAGUE)
