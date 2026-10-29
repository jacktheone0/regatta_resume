"""The reparse-results management command."""
from datetime import date

from models import Regatta, Result, Sailor


def seed(db):
    regatta = Regatta(name='Old', start_date=date(2025, 6, 1), external_id='cli1', status='final')
    jane = Sailor(name='Jane Doe', name_normalized='jane doe')
    db.session.add_all([regatta, jane])
    db.session.flush()
    db.session.add(Result(sailor_id=jane.id, regatta_id=regatta.id, placement=1, division='A',
                          raw_row_data='1 | Jane Doe\nJim Roe | 2.0'))
    db.session.commit()


def test_dry_run_prints_report_and_changes_nothing(runner, db):
    seed(db)
    result = runner.invoke(args=['reparse-results', '--dry-run'])
    assert result.exit_code == 0, result.output
    assert '[DRY RUN]' in result.output
    assert 'crew_rows_added=1' in result.output
    assert Result.query.count() == 1


def test_apply_writes(runner, db):
    seed(db)
    result = runner.invoke(args=['reparse-results'])
    assert result.exit_code == 0, result.output
    assert '[APPLIED]' in result.output
    assert Result.query.count() == 2
