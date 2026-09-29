"""Codex review: partial feeds, frozen calls and read-only rendering."""
from datetime import datetime, timezone
import re

import pytest

import build_report as b
import fetch_data
import prediction_log
import projection_log
import report_records
from conftest import LEAGUE, a_season, add_understat_matches
from test_fixture_tape import a_fotmob_league
from test_projection_trend import _render


def test_zero_trend_has_a_finite_flat_line(db):
    add_understat_matches(db, 'Balanced', 6, npxgd=0)
    html = b.rolling_sparklines(db, LEAGUE)
    assert "points='0.0,32.0 220.0,32.0'" in html


def test_partial_measurements_do_not_count_as_zero(db):
    a_fotmob_league(db)
    db.execute("UPDATE fotmob_team_matches SET tackles=NULL, xg_set_play=NULL "
               "WHERE team='Club 0' AND match_id='Club 0-h-0'")
    row = b.fixture_tape(db, LEAGUE)['h']['Club 0']
    assert row[2][0] == 20
    assert row[3][0] == 25
    db.execute("UPDATE fotmob_team_matches SET tackles=NULL "
               "WHERE match_id='Club 0-h-1'")
    assert b.fixture_tape(db, LEAGUE)['h']['Club 0'][2] == [None, None]


@pytest.mark.parametrize('column', ['saves', 'goals_conceded'])
def test_keeper_with_one_missing_count_is_not_a_zero(db, column):
    a_season(db)
    db.execute(f'UPDATE fotmob_match_players SET {column}=NULL WHERE is_gk=1')
    assert b.keeper_rows(db, LEAGUE) == ([], 12)


def call(rows, hour, **kw):
    return prediction_log.record(
        rows, '2026-09-28', '42', LEAGUE, '2026', '2026-09-28', 'A', 'B',
        (.5, .3, .2), (1.5, 1), match_time=kw.pop('match_time', '18:00:00'),
        now=datetime(2026, 9, 28, hour, tzinfo=timezone.utc),
        model_version='test-v1', **kw)


def test_calls_freeze_at_kickoff_even_without_a_score():
    rows = {}
    assert call(rows, 17)
    before = rows['42'].copy()
    assert not call(rows, 18)
    assert not call(rows, 19)
    assert rows['42'] == before
    assert before['last_recorded_at'] == '2026-09-28T17:00:00+00:00'
    assert before['model_version'] == 'test-v1'


def test_unknown_kickoff_is_not_permission_to_update_after_play():
    rows = {}
    assert not call(rows, 1, match_time=None)
    assert not call(rows, 1, match_time='bad')
    assert rows == {}


def test_revised_time_does_not_unfreeze_an_old_call():
    rows = {}
    call(rows, 17)
    assert not call(rows, 19, match_time='20:00:00')


def test_legacy_log_does_not_invent_first_publication_time(tmp_path):
    rows = {'42': {'event_id': '42', 'first_seen': '2026-09-20'}}
    call(rows, 17)
    assert 'first_recorded_at' not in rows['42']
    # Real legacy rows contain these fields; new CSV columns can be blank.
    rows['42'].update(league=LEAGUE, home='A', season='2026')
    target = tmp_path / 'log.csv'
    prediction_log.save(rows, target)
    assert prediction_log.load(target)['42']['first_recorded_at'] == ''


def test_staged_records_are_visible_without_writing(monkeypatch):
    monkeypatch.setattr(projection_log, 'load', lambda: {})
    writes = []
    monkeypatch.setattr(projection_log, 'save', lambda rows: writes.append(rows))
    report_records.stage(projection_log, {'ignored': {}})
    with report_records.capture() as pending:
        report_records.stage(projection_log, {'today': {'value': 1}})
        assert report_records.load(projection_log)['today']['value'] == 1
        assert writes == []
    assert report_records.load(projection_log) == {}
    report_records.persist(pending)
    assert len(writes) == 1


def test_failed_build_never_persists_records(monkeypatch):
    writes = []
    def fail(**kw):
        report_records.stage(projection_log, {'today': {}})
        raise RuntimeError('archive failed')
    monkeypatch.setattr(b, '_build', fail)
    monkeypatch.setattr(b.sys, 'argv', ['build_report.py', '--publish'])
    monkeypatch.setattr(projection_log, 'save', lambda rows: writes.append(rows))
    with pytest.raises(RuntimeError):
        b.main()
    assert writes == []


def test_preview_never_persists_records(monkeypatch):
    writes = []
    def render(publish):
        assert publish is False
        report_records.stage(projection_log, {'today': {}})
    monkeypatch.setattr(b, '_build', render)
    monkeypatch.setattr(b.sys, 'argv', ['build_report.py'])
    monkeypatch.setattr(projection_log, 'save', lambda rows: writes.append(rows))
    b.main()
    assert writes == []


@pytest.mark.parametrize('response', [RuntimeError('offline'), {'events': None}, {}])
def test_failed_or_truncated_round_prevents_publication(db, monkeypatch, tmp_path, response):
    db.execute("INSERT INTO matches(event_id,league,season,round) VALUES('42',?,'2026',1)", (LEAGUE,))
    db.commit()
    def fetch(url):
        if 'lookuptable' in url:
            return {'table': []}
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(fetch_data, 'fetch_json', fetch)
    monkeypatch.setattr(fetch_data.time, 'sleep', lambda _: None)
    monkeypatch.setattr(fetch_data, 'DATA_DIR', tmp_path)
    with pytest.raises(RuntimeError, match='incomplete fixture fetch'):
        fetch_data.fetch_league(db, LEAGUE, {'season': '2026', 'id': '1', 'rounds': 1}, 'now')
    assert db.execute('SELECT event_id FROM matches').fetchall() == [('42',)]


def test_projection_horizontal_spacing_uses_elapsed_days(db, monkeypatch):
    html = _render(db, monkeypatch, ['2026-04-01', '2026-04-02', '2026-04-11'])
    points = re.search(r"class='spark-line [^']+' points='([^']+)'", html).group(1)
    assert [float(p.split(',')[0]) for p in points.split()] == [0, 22, 220]
    # the per-panel scale and date lines were forty repeated lines of text
    # on a twenty-panel chart; the span is stated once, in the legend
    assert 'Vertical scale:' not in html
    assert 'calendar days' in html
    assert html.count('from 1 Apr to 11 Apr') == 1
    assert 'flat stretches are the days between rounds' not in html


def test_complete_fixture_fetch_still_succeeds(db, monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_data, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(fetch_data.time, 'sleep', lambda _: None)
    def fetch(url):
        if 'lookuptable' in url:
            return {'table': []}
        round_number = int(url.split('&r=')[1].split('&')[0])
        return {'events': [{'idEvent': str(round_number), 'strHomeTeam': 'A',
                            'strAwayTeam': 'B', 'strSeason': '2026',
                            'intRound': str(round_number)}]}
    monkeypatch.setattr(fetch_data, 'fetch_json', fetch)
    fetch_data.fetch_league(db, LEAGUE, {'season': '2026', 'id': '1', 'rounds': 2}, 'now')
    assert db.execute('SELECT COUNT(*) FROM matches').fetchone()[0] == 2


def test_missing_new_round_is_detected_without_cached_history(db, monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_data, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(fetch_data.time, 'sleep', lambda _: None)
    def fetch(url):
        if 'lookuptable' in url:
            return {'table': []}
        if '&r=1&' in url:
            return {'events': [{'idEvent': '1', 'strHomeTeam': 'A', 'strAwayTeam': 'B'}]}
        return {'events': []}
    monkeypatch.setattr(fetch_data, 'fetch_json', fetch)
    with pytest.raises(RuntimeError, match='incomplete fixture fetch'):
        fetch_data.fetch_league(db, LEAGUE, {'season': '2026', 'id': '1', 'rounds': 2}, 'now')
    assert db.execute('SELECT COUNT(*) FROM matches').fetchone()[0] == 0
