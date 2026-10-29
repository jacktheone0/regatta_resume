"""
Pages render with the new data shapes: NULL placements, in-progress
regattas, crew rows, normalized stat cards.
"""
from datetime import date, datetime, timedelta

from models import (CollegeResult, HSResult, Regatta, ResumeLink, Result, Sailor,
                    SailorName)


def seed_sailor(db):
    sailor = Sailor(name='Jane Doe', name_normalized='jane doe', home_club='SYC')
    db.session.add(sailor)
    final = Regatta(name='Spring Open', start_date=date(2026, 4, 1), end_date=date(2026, 4, 2),
                    external_id='r1', status='final', location='Bay YC')
    series = Regatta(name='Fall Series', start_date=date.today() - timedelta(days=10),
                     end_date=date.today() + timedelta(days=40), external_id='r2',
                     status='in_progress', results_as_of=datetime.utcnow())
    db.session.add_all([final, series])
    db.session.flush()
    db.session.add_all([
        Result(sailor_id=sailor.id, regatta_id=final.id, placement=2, fleet_size=18,
               division='A', role='skipper', boat_type='420', crew_partner='Jim Roe'),
        Result(sailor_id=sailor.id, regatta_id=final.id, placement=None, scoring_code='DNF',
               fleet_size=18, division='B', role='crew', boat_type='420'),
        Result(sailor_id=sailor.id, regatta_id=series.id, placement=1, fleet_size=9,
               division='A', role='skipper', boat_type='Laser'),
    ])
    name = SailorName(name='Jane Doe', name_normalized='jane doe', source='both', school='Bay High')
    db.session.add(name)
    db.session.flush()
    db.session.add(HSResult(sailor_name_id=name.id, regatta_name='HS Invite',
                            regatta_date=date(2025, 10, 1), place='21/32 (B Div)',
                            place_numeric=21, total_boats=32, position='Skipper', division='B Div'))
    db.session.add(CollegeResult(sailor_name_id=name.id, regatta_name='College Open',
                                 regatta_date=date(2026, 3, 1), place='3/12',
                                 place_numeric=3, total_boats=12, position='Crew', division='A Div'))
    db.session.commit()
    return sailor, name


def test_sailor_profile_shows_fleet_sizes_codes_and_in_progress(client, db):
    sailor, _ = seed_sailor(db)
    page = client.get(f'/sailor/{sailor.id}')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert '2 of 18' in html
    assert 'DNF' in html
    assert 'In progress' in html
    assert 'with Jim Roe' in html
    assert 'Avg Finish per 100 Boats' in html
    assert 'HS Invite' in html and 'College Open' in html
    # 2/18 -> 11.1, 21/32 -> 65.6, 3/12 -> 25.0 ; DNF and in-progress excluded
    assert '33.9' in html
    assert 'still in progress' in html


def test_sailor_profile_without_results(client, db):
    sailor = Sailor(name='New Sailor', name_normalized='new sailor')
    db.session.add(sailor)
    db.session.commit()
    page = client.get(f'/sailor/{sailor.id}')
    assert page.status_code == 200
    assert 'N/A' in page.get_data(as_text=True)


def test_coach_view_renders_standardized_chart_and_breakdowns(client, db):
    sailor, _ = seed_sailor(db)
    page = client.get(f'/coach-view/{sailor.id}?months=12')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'finish per 100 boats' in html
    assert '"standardized": 11.1' in html
    assert 'By Boat Class' in html and 'By Source' in html
    assert 'High School' in html
    assert 'In progress' in html


def test_coach_view_boat_filter(client, db):
    sailor, _ = seed_sailor(db)
    page = client.get(f'/coach-view/{sailor.id}?boat=420')
    assert page.status_code == 200
    assert 'in 420' in page.get_data(as_text=True)


def test_sailor_name_profile_uses_normalized_stats(client, db):
    _, name = seed_sailor(db)
    page = client.get(f'/sailor-name/{name.id}')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert '3 of 12' in html
    assert 'Avg Finish per 100 Boats' in html
    assert '45.3' in html     # mean of 65.6 and 25.0


def test_shared_resume_tolerates_rows_without_a_place(client, db):
    sailor, _ = seed_sailor(db)
    link = ResumeLink(sailor_id=sailor.id, token='tok123', template_style='modern')
    db.session.add(link)
    db.session.commit()
    page = client.get('/resume/tok123')
    assert page.status_code == 200
    assert 'DNF' in page.get_data(as_text=True)


def test_index_and_search(client, db):
    seed_sailor(db)
    assert client.get('/').status_code == 200
    search = client.get('/search?q=jane')
    assert search.status_code == 200
    assert 'Jane Doe' in search.get_data(as_text=True)


def test_json_apis_carry_fleet_size_and_normalized_stats(client, db):
    sailor, _ = seed_sailor(db)
    stats = client.get(f'/api/sailors/{sailor.id}/stats').get_json()
    assert stats['counted_results'] == 3
    assert stats['avg_standardized'] == 33.9
    results = client.get(f'/api/sailors/{sailor.id}/results').get_json()
    by_place = {r['place_text']: r for r in results}
    assert by_place['2 of 18']['fleet_size'] == 18
    assert by_place['2 of 18']['standardized'] == 11.1
    assert by_place['DNF']['placement'] is None
    assert by_place['DNF']['scoring_code'] == 'DNF'
    assert by_place['1 of 9']['regatta']['status'] == 'in_progress'
