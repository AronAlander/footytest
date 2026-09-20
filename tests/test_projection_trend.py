"""The "Projection over time" panels.

The block was misread as a league table: its rank badge was the projected
finishing position with nothing beside it saying so, and the only place the
projected points appeared was a hover tooltip. These tests hold the panel to
saying both numbers, and to marking where the season began — before the
first match the projection has nothing to move on, and a flat line there
means "nothing had happened", not "nothing changed".
"""
import build_report
import projection_log
from conftest import LEAGUE

# six is the fewest the projection will model (PROJECT_MIN_TEAMS)
CLUBS = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"]


def _fixtures(db, played_rounds=3, rounds=6, season="2026"):
    """A tiny league: every club plays, the first rounds have results."""
    mid = 0
    for r in range(rounds):
        day = f"2026-{4 + r:02d}-01"
        for h, a in ((CLUBS[0], CLUBS[1]), (CLUBS[2], CLUBS[3]),
                     (CLUBS[4], CLUBS[5])):
            mid += 1
            done = r < played_rounds
            # Alpha and Gamma win everything that has been played, so they
            # lead the table; strengths below decide who is projected to
            db.execute(
                "INSERT INTO matches (event_id, league, season, match_date, "
                "home_team, away_team, home_score, away_score) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (str(mid), LEAGUE, season, day, h, a,
                 2 if done else None, 0 if done else None))
            if not done:
                continue
            for team, opp, side, gf, ga in ((h, a, "h", 2, 0), (a, h, "a", 0, 2)):
                # Beta creates far more than it has scored, so the simulation
                # rates it above its results; Alpha is the reverse
                strong = team in (CLUBS[1], CLUBS[3], CLUBS[5])
                db.execute(
                    "INSERT INTO fotmob_team_matches (season, league, match_id, "
                    "team, opponent, match_date, home_away, scored, missed, "
                    "xg, xga, npxg, npxga) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (season, LEAGUE, str(mid), team, opp, day, side, gf, ga,
                     2.4 if strong else 0.5, 0.4 if strong else 2.2,
                     2.3 if strong else 0.4, 0.3 if strong else 2.1))
    db.commit()


def _log(monkeypatch, dates, season="2026", points=None):
    """A projection log with one snapshot per date for every club."""
    rows = {}
    for d in dates:
        for k, club in enumerate(CLUBS):
            rows[f"{d}|{LEAGUE}|{club}"] = {
                "date": d, "league": LEAGUE, "season": season, "team": club,
                "proj_pts": f"{(points or {}).get(club, 60 - 5 * k):.1f}",
                "title_pct": "0.25", "top4_pct": "0.5", "bottom3_pct": "0.1",
            }
    monkeypatch.setattr(projection_log, "load", lambda *a, **k: rows)
    monkeypatch.setattr(projection_log, "save", lambda *a, **k: None)


def _render(db, monkeypatch, dates):
    _fixtures(db)
    _log(monkeypatch, dates)
    monkeypatch.setattr(build_report, "PROJECT_SIMS", 200, raising=False)
    return build_report.season_projection_trend(db, LEAGUE)


def test_every_panel_says_both_where_it_is_and_where_it_is_heading(db, monkeypatch):
    html = _render(db, monkeypatch, ["2026-03-01", "2026-04-02", "2026-05-02"])
    assert "now" in html and "projected" in html
    assert "pts</span>" in html
    # the projected points are on the panel, not only in a hover title
    assert html.count("class='proj'") == len(CLUBS)


def test_the_projected_points_are_the_number_on_the_panel(db, monkeypatch):
    html = _render(db, monkeypatch, ["2026-03-01", "2026-04-02", "2026-05-02"])
    r = build_report._compute_projection(db, LEAGUE)
    for i in r["order"]:
        assert f"{r['proj'][i]:.0f} pts" in html


def test_a_club_above_its_projection_is_marked_as_falling(db, monkeypatch):
    """The arrow follows the move, not the points delta.

    It used to take its colour from the change since the first snapshot, so
    a club sliding from 3rd to 8th while its projected points rose got a
    green downward arrow.
    """
    html = _render(db, monkeypatch, ["2026-03-01", "2026-04-02", "2026-05-02"])
    r = build_report._compute_projection(db, LEAGUE)
    for rank, i in enumerate(r["order"], 1):
        move = r["now_rank"][i] - rank
        if move < 0:                       # projected to finish lower
            assert f"down'>▼{-move}" in html
        elif move > 0:
            assert f"up'>▲{move}" in html


def test_the_season_start_is_marked_when_snapshots_predate_it(db, monkeypatch):
    """Two nights before a ball was kicked, so the flat run is explained."""
    html = _render(db, monkeypatch,
                   ["2026-03-01", "2026-03-15", "2026-04-02", "2026-05-02"])
    assert html.count("spark-kick") == len(CLUBS)
    assert "first match of the season" in html


def test_no_mark_when_the_log_starts_with_the_season(db, monkeypatch):
    """Allsvenskan's log began on matchday one: nothing to mark."""
    html = _render(db, monkeypatch, ["2026-04-01", "2026-04-02", "2026-05-02"])
    assert "spark-kick" not in html


def test_a_club_missing_from_the_log_does_not_shift_the_ranks(db, monkeypatch):
    """The badge is a position in the projected table, not a row number."""
    _fixtures(db)
    dates = ["2026-03-01", "2026-04-02", "2026-05-02"]
    _log(monkeypatch, dates)
    rows = projection_log.load()
    for key in [k for k in rows if rows[k]["team"] == CLUBS[0]]:
        del rows[key]
    monkeypatch.setattr(projection_log, "load", lambda *a, **k: rows)
    monkeypatch.setattr(build_report, "PROJECT_SIMS", 200, raising=False)
    html = build_report.season_projection_trend(db, LEAGUE)
    r = build_report._compute_projection(db, LEAGUE)
    shown = [(rank, i) for rank, i in enumerate(r["order"], 1)
             if r["teams"][i] != CLUBS[0]]
    for rank, i in shown:
        assert f"projected {build_report.ordinal(rank)}" in html
