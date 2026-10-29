"""Which catalogue rows a run visits, with in-progress series and provisional finals."""
from datetime import date, datetime, timedelta

import clubspot_catalog as catalog
from models import ClubspotRegattaCheck

NOW = datetime(2026, 10, 29, 12, 0, 0)
TODAY = NOW.date()


def row(db, external_id, start, end=None, checked=None, results=0):
    check = ClubspotRegattaCheck(external_id=external_id, name=external_id, start_date=start,
                                 end_date=end, last_checked_at=checked, result_count=results,
                                 check_count=1 if checked else 0)
    db.session.add(check)
    db.session.commit()
    return check


def due_ids(**kwargs):
    due, counts = catalog.select_due(now=NOW, **kwargs)
    return [c.external_id for c in due], counts


def test_upcoming_is_skipped_and_never_visited_final_is_due(db):
    row(db, 'future', TODAY + timedelta(days=3), TODAY + timedelta(days=4))
    row(db, 'done', TODAY - timedelta(days=3), TODAY - timedelta(days=2))
    ids, counts = due_ids()
    assert ids == ['done']
    assert counts['catalog'] == 2 and counts['due'] == 1


def test_in_progress_series_is_due_daily_even_with_results(db):
    row(db, 'series', TODAY - timedelta(days=20), TODAY + timedelta(days=30),
        checked=NOW - timedelta(days=1, minutes=1), results=40)
    row(db, 'series-fresh', TODAY - timedelta(days=20), TODAY + timedelta(days=30),
        checked=NOW - timedelta(hours=3), results=40)
    ids, counts = due_ids()
    assert ids == ['series']
    assert counts['in_progress'] == 2
    assert counts['with_results'] == 2


def test_final_with_results_read_before_it_ended_is_refreshed_once(db):
    end = TODAY - timedelta(days=5)
    row(db, 'provisional', end - timedelta(days=30), end,
        checked=datetime.combine(end - timedelta(days=3), datetime.min.time()), results=20)
    row(db, 'settled', end - timedelta(days=30), end,
        checked=datetime.combine(end + timedelta(days=1), datetime.min.time()), results=20)
    ids, counts = due_ids()
    assert ids == ['provisional']
    assert counts['refresh_final'] == 1


def test_empty_final_rechecked_inside_window_not_more_than_interval(db):
    row(db, 'recent-empty', TODAY - timedelta(days=10), TODAY - timedelta(days=9),
        checked=NOW - timedelta(days=3))
    row(db, 'just-checked', TODAY - timedelta(days=10), TODAY - timedelta(days=9),
        checked=NOW - timedelta(hours=5))
    row(db, 'old-empty', TODAY - timedelta(days=100), TODAY - timedelta(days=99),
        checked=NOW - timedelta(days=30))
    ids, _ = due_ids(recheck_window_days=30, recheck_interval_days=2)
    assert ids == ['recent-empty']


def test_placeholder_end_date_long_after_start_counts_as_final(db, monkeypatch):
    monkeypatch.setenv('SCRAPER_IN_PROGRESS_MAX_DAYS', '120')
    row(db, 'stale', TODAY - timedelta(days=200), TODAY + timedelta(days=500))
    ids, counts = due_ids()
    assert ids == ['stale']
    assert counts['in_progress'] == 0


def test_ordering_newest_first_and_undated_last(db):
    row(db, 'undated', None)
    row(db, 'older', TODAY - timedelta(days=30), TODAY - timedelta(days=29))
    row(db, 'newer', TODAY - timedelta(days=2), TODAY - timedelta(days=1))
    ids, _ = due_ids()
    assert ids == ['newer', 'older', 'undated']
    ids, _ = due_ids(limit=1)
    assert ids == ['newer']
