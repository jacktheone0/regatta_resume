"""Normalized statistics on plain entry dicts (no database)."""
from datetime import date

import stats
from stats import _entry, breakdown, eligible, percentile, standardized_finish, summarize, trends

TODAY = date(2026, 10, 29)


def e(place, fleet, days_ago=10, **extra):
    from datetime import timedelta
    return _entry(extra.pop('source', 'clubspot'), TODAY - timedelta(days=days_ago),
                  extra.pop('regatta', f'R{place}of{fleet}'), place, fleet, **extra)


def test_standardized_finish_and_percentile():
    assert standardized_finish(3, 6) == 50.0
    assert standardized_finish(15, 30) == 50.0
    assert standardized_finish(1, 40) == 2.5
    assert standardized_finish(3, None) is None
    assert standardized_finish(None, 10) is None
    assert percentile(1, 10) == 100.0
    assert percentile(10, 10) == 0.0
    assert percentile(1, 1) is None


def test_eligible_separates_each_exclusion_reason():
    entries = [e(1, 20), e(2, 3), e(None, 10, scoring_code='DNF'), e(4, None),
               e(1, 30, in_progress=True)]
    counted, excluded = eligible(entries, floor=5)
    assert len(counted) == 1
    assert excluded == {'small_fleet': 1, 'no_finish': 1, 'no_fleet_size': 1, 'in_progress': 1}


def test_summarize_keeps_legacy_keys_and_adds_normalized_ones():
    entries = [e(1, 20, days_ago=5, boat_type='420', role='skipper'),
               e(10, 20, days_ago=400, boat_type='420', role='crew'),
               e(2, 4, days_ago=20, boat_type='Laser'),       # small fleet: raw only
               e(5, 50, days_ago=30, source='hs')]
    s = summarize(entries, today=TODAY)
    assert s['total_regattas'] == 4
    assert s['counted_results'] == 3
    assert s['excluded'] == {'small_fleet': 1}
    assert s['best_finish'] == 1 and s['best_finish_text'] == '1 of 20'
    assert s['average_placement'] == 4.5
    assert s['top_3_count'] == 2 and s['top_10_count'] == 4
    assert s['best_standardized'] == 5.0
    assert s['avg_standardized'] == round((5.0 + 50.0 + 10.0) / 3, 1)
    assert s['weighted_standardized'] == round((5.0 * 20 + 50.0 * 20 + 10.0 * 50) / 90, 1)
    assert s['most_sailed_fleet'] == '420'
    assert s['skipper_count'] == 1 and s['crew_count'] == 1
    assert s['by_source'] == {'clubspot': 3, 'hs': 1, 'college': 0}
    assert s['years_active'] == 2
    assert s['recent_regattas'] == 3


def test_summarize_handles_no_entries():
    s = summarize([], today=TODAY)
    assert s['total_regattas'] == 0
    assert s['best_finish'] is None and s['avg_standardized'] is None
    assert s['years_active'] is None


def test_breakdown_groups_and_sorts_by_count():
    entries = [e(1, 10, boat_type='420'), e(5, 10, boat_type='420'), e(2, 8, boat_type='Laser')]
    rows = breakdown(entries, 'boat_type')
    assert [r['name'] for r in rows] == ['420', 'Laser']
    assert rows[0]['count'] == 2 and rows[0]['avg_standardized'] == 30.0
    assert rows[0]['best_standardized'] == 10.0


def test_trends_compare_recent_to_historical_on_standardized_finish():
    entries = [e(1, 10, days_ago=10), e(2, 10, days_ago=20),    # recent: 10, 20
               e(5, 10, days_ago=300), e(6, 10, days_ago=400),  # historical: 50, 60
               e(1, 10, days_ago=15, in_progress=True)]         # excluded
    t = trends(entries, months_back=6, today=TODAY)
    assert t['recent_avg'] == 15.0 and t['historical_avg'] == 55.0
    assert t['improvement'] == 40.0
    assert t['recent_top_3'] == 2
    assert t['recent_count'] == 2 and t['historical_count'] == 2
    assert t['excluded'] == {'in_progress': 1}
    assert [p['standardized'] for p in t['chart_data']] == [60.0, 50.0, 20.0, 10.0]
    assert t['chart_data'][0]['fleet'] == 10


def test_trends_boat_type_filter():
    entries = [e(1, 10, boat_type='420'), e(5, 10, boat_type='Laser')]
    t = trends(entries, boat_type='Laser', today=TODAY)
    assert t['recent_avg'] == 50.0 and t['recent_count'] == 1


def test_min_fleet_from_environment(monkeypatch):
    monkeypatch.setenv('STATS_MIN_FLEET', '2')
    assert stats.min_fleet() == 2
    counted, _ = eligible([e(1, 3)])
    assert len(counted) == 1
