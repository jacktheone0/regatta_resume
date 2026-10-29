"""Re-deriving crew rows and fleet sizes from stored raw row text."""
from datetime import date

from models import ClubspotRegattaCheck, Regatta, Result, Sailor
from reparse import reparse_all


def seed(db):
    regatta = Regatta(name='Old Regatta', start_date=date(2025, 6, 1), external_id='old1', status='final')
    db.session.add(regatta)
    jane = Sailor(name='Jane Doe', name_normalized='jane doe')
    ann = Sailor(name='Ann Lee', name_normalized='ann lee')
    db.session.add_all([jane, ann])
    db.session.flush()
    # Two boats in division A; the old scraper stored skippers only
    db.session.add(Result(sailor_id=jane.id, regatta_id=regatta.id, placement=1, division='A',
                          raw_row_data='1 | 101 | Jane Doe\nJim Roe | 3.0', points_scored=3.0))
    db.session.add(Result(sailor_id=ann.id, regatta_id=regatta.id, placement=2, division='A',
                          raw_row_data='2 | 102 | Ann Lee | 5.0', points_scored=5.0))
    db.session.add(ClubspotRegattaCheck(external_id='old1', start_date=date(2025, 6, 1),
                                        last_checked_at=date(2025, 6, 2) and None, result_count=2))
    db.session.commit()
    return regatta


def test_dry_run_reports_without_writing(db):
    seed(db)
    report = reparse_all(dry_run=True)
    assert report['regattas'] == 1 and report['rows'] == 2
    assert report['fleet_sizes'] == 2
    assert report['crew_added'] == 1
    assert report['sailors_added'] == 1
    assert Result.query.count() == 2
    assert Sailor.query.count() == 2
    assert all(r.fleet_size is None for r in Result.query.all())


def test_apply_adds_crew_rows_and_fleet_sizes(db):
    regatta = seed(db)
    report = reparse_all(dry_run=False)
    assert report['crew_added'] == 1
    rows = Result.query.filter_by(regatta_id=regatta.id).order_by(Result.id).all()
    assert len(rows) == 3
    assert all(r.fleet_size == 2 for r in rows)
    assert rows[0].role == 'skipper' and rows[0].crew_partner == 'Jim Roe'
    crew = rows[2]
    assert crew.sailor.name == 'Jim Roe' and crew.role == 'crew'
    assert crew.placement == 1 and crew.crew_partner == 'Jane Doe'
    assert crew.raw_row_data == rows[0].raw_row_data

    # Running again changes nothing
    again = reparse_all(dry_run=False)
    assert again['crew_added'] == 0 and again['fleet_sizes'] == 0
    assert Result.query.count() == 3


def test_requeue_marks_regattas_without_raw_text(db):
    regatta = seed(db)
    jane = Sailor.query.filter_by(name_normalized='jane doe').one()
    db.session.add(Result(sailor_id=jane.id, regatta_id=regatta.id, placement=3, division='B',
                          raw_row_data=None))
    db.session.commit()

    report = reparse_all(dry_run=False, requeue_missing=True)
    assert report['missing_raw'] == 1 and report['requeued'] == 1
    check = db.session.get(ClubspotRegattaCheck, 'old1')
    assert check.last_checked_at is None and check.result_count == 0


def test_regatta_filter(db):
    regatta = seed(db)
    other = Regatta(name='Other', start_date=date(2025, 7, 1), external_id='other', status='final')
    db.session.add(other)
    db.session.commit()
    report = reparse_all(dry_run=True, regatta_id=regatta.id)
    assert report['regattas'] == 1
