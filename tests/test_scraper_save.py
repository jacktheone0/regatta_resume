"""
How the scraper stores what it parsed: one row per person, final results
never rewritten, in-progress standings upserted, legacy rows completed.
No browser and no network: _save_result and _get_or_create_regatta only.
"""
from datetime import date, datetime, timedelta

import pytest

from models import Regatta, Result, Sailor
from scraper import ClubspotScraper


def person(name, role='skipper', place=1, division='A', fleet=5, **extra):
    return dict(sailor_name=name, role=role, placement=place, division=division,
                fleet_size=fleet, scoring_code=None, boat_type='420', points_scored=3.0,
                team_name=None, crew_partner=extra.get('crew_partner'),
                raw_row_data=extra.get('raw', f'{place} | {name} | 3.0'))


@pytest.fixture
def scraper(db):
    return ClubspotScraper()


def make_regatta(scraper, status='final', external_id='abc123', start=None, end=None):
    start = start or date(2026, 10, 1)
    return scraper._get_or_create_regatta({
        'external_id': external_id, 'name': 'Fall Series', 'start_date': start,
        'end_date': end, 'status': status, 'source_url': 'https://x/' + external_id})


def test_skipper_and_crew_each_get_a_row(db, scraper):
    regatta = make_regatta(scraper)
    scraper._save_result(person('Jane Doe', crew_partner='Jim Roe'), regatta)
    scraper._save_result(person('Jim Roe', role='crew', crew_partner='Jane Doe'), regatta)
    rows = Result.query.order_by(Result.id).all()
    assert [(r.sailor.name, r.role, r.placement, r.fleet_size) for r in rows] == [
        ('Jane Doe', 'skipper', 1, 5), ('Jim Roe', 'crew', 1, 5)]
    assert rows[0].place_text == '1 of 5'
    assert scraper.stats['results_added'] == 2
    assert scraper.stats['sailors_added'] == 2


def test_same_person_skipper_in_one_division_crew_in_another(db, scraper):
    regatta = make_regatta(scraper)
    scraper._save_result(person('Jane Doe', division='A'), regatta)
    scraper._save_result(person('Jane Doe', role='crew', division='B', place=4), regatta)
    assert Result.query.count() == 2
    assert Sailor.query.count() == 1


def test_final_regatta_is_insert_only_but_fills_legacy_gaps(db, scraper):
    regatta = make_regatta(scraper, status='final')
    legacy = Result(sailor_id=scraper._get_or_create_sailor('Jane Doe').id, regatta_id=regatta.id,
                    placement=7, division='A', role=None, fleet_size=None, raw_row_data=None)
    db.session.add(legacy)
    db.session.commit()

    scraper._save_result(person('Jane Doe', place=2, fleet=12, crew_partner='Jim Roe'), regatta)

    rows = Result.query.all()
    assert len(rows) == 1
    assert rows[0].placement == 7          # the stored finish stands
    assert rows[0].fleet_size == 12        # gaps are filled
    assert rows[0].role == 'skipper'
    assert rows[0].crew_partner == 'Jim Roe'
    assert rows[0].raw_row_data
    assert scraper.stats['results_updated'] == 1
    assert scraper.stats['results_added'] == 0


def test_in_progress_regatta_upserts_standings(db, scraper):
    regatta = make_regatta(scraper, status='in_progress', end=date.today() + timedelta(days=30))
    scraper._save_result(person('Jane Doe', place=5, fleet=10), regatta)
    scraper._save_result(person('Jane Doe', place=2, fleet=11), regatta)
    rows = Result.query.all()
    assert len(rows) == 1
    assert rows[0].placement == 2 and rows[0].fleet_size == 11
    assert scraper.stats['results_updated'] == 1


def test_unchanged_in_progress_row_is_not_counted_as_updated(db, scraper):
    regatta = make_regatta(scraper, status='in_progress')
    scraper._save_result(person('Jane Doe'), regatta)
    scraper._save_result(person('Jane Doe'), regatta)
    assert scraper.stats['results_updated'] == 0


def test_dnf_is_stored_with_code_and_no_place(db, scraper):
    regatta = make_regatta(scraper)
    data = person('Jane Doe', place=None)
    data['scoring_code'] = 'DNF'
    scraper._save_result(data, regatta)
    row = Result.query.one()
    assert row.placement is None and row.scoring_code == 'DNF'
    assert row.place_text == 'DNF'


def test_regatta_status_and_results_as_of_follow_each_visit(db, scraper):
    before = datetime.utcnow() - timedelta(seconds=1)
    regatta = make_regatta(scraper, status='in_progress')
    assert regatta.status == 'in_progress'
    assert regatta.results_as_of >= before
    again = make_regatta(scraper, status='final', end=date(2026, 10, 20))
    assert again.id == regatta.id
    assert again.status == 'final' and again.end_date == date(2026, 10, 20)
    assert Regatta.query.count() == 1


def test_result_without_name_is_ignored(db, scraper):
    regatta = make_regatta(scraper)
    scraper._save_result(dict(person('x'), sailor_name=''), regatta)
    assert Result.query.count() == 0
