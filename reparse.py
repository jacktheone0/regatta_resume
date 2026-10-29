"""
Re-derive crew rows, scoring codes and fleet sizes for ClubSpot results
already in the database, from the raw row text saved with each result.

Every result row the scraper has ever written carries the pipe-joined cells
of the table row it came from, so the people aboard and the boat count per
division can be recovered without visiting ClubSpot again. Rows that have no
raw text (none are expected, but the column is nullable) cannot be
re-parsed; --requeue-missing marks their regattas unchecked in the catalogue
so the next scraper run visits them.

Rules, matching the scraper's own save logic:
  - a stored finish is never changed; only fleet_size, scoring_code, role
    and crew_partner are filled when empty,
  - crew rows are added only when no row exists for that sailor, regatta,
    division and role,
  - fleet size = distinct raw rows in the division (one row per boat).
"""
from collections import defaultdict
from datetime import datetime

from models import db, Sailor, Regatta, Result, ClubspotRegattaCheck
from clubspot_common import _parse_by_position, count_boats


def _people_from_raw(raw):
    """Everyone aboard, skipper first, from a stored ' | '-joined row"""
    return _parse_by_position([c.strip() for c in raw.split('|')])


def _get_or_create_sailor(name, report, dry_run):
    normalized = name.lower().strip()
    sailor = Sailor.query.filter_by(name_normalized=normalized).first()
    if sailor:
        return sailor
    report['sailors_added'] += 1
    if dry_run:
        return None
    sailor = Sailor(name=name.strip(), name_normalized=normalized)
    db.session.add(sailor)
    db.session.flush()
    return sailor


def reparse_regatta(regatta, dry_run, report):
    rows = Result.query.filter_by(regatta_id=regatta.id).all()
    report['regattas'] += 1
    report['rows'] += len(rows)

    # One boat per distinct raw row, per division
    boats = [{'division': r.division, 'raw_row_data': r.raw_row_data} for r in rows if r.raw_row_data]
    sizes = count_boats(boats)

    existing = {(r.sailor_id, r.division, r.role or 'skipper') for r in rows}
    missing_raw = False

    for row in rows:
        if not row.raw_row_data:
            missing_raw = True
            report['missing_raw'] += 1
            continue
        size = sizes.get(row.division)
        if row.fleet_size is None and size:
            report['fleet_sizes'] += 1
            if not dry_run:
                row.fleet_size = size
        if row.role is None:
            if not dry_run:
                row.role = 'skipper'

        people = _people_from_raw(row.raw_row_data)
        if not people:
            continue
        skipper = people[0]
        if row.scoring_code is None and skipper['scoring_code'] and row.placement is None:
            report['scoring_codes'] += 1
            if not dry_run:
                row.scoring_code = skipper['scoring_code']
        if row.crew_partner is None and skipper['crew_partner'] and (row.role or 'skipper') == 'skipper':
            if not dry_run:
                row.crew_partner = skipper['crew_partner']

        # Crew rows: only from the skipper's row, so a crew row is not re-expanded
        if (row.role or 'skipper') != 'skipper':
            continue
        for person in people[1:]:
            sailor = _get_or_create_sailor(person['sailor_name'], report, dry_run)
            key = (sailor.id if sailor else ('new', person['sailor_name'].lower()), row.division, 'crew')
            if key in existing:
                continue
            existing.add(key)
            report['crew_added'] += 1
            if dry_run or sailor is None:
                continue
            db.session.add(Result(
                sailor_id=sailor.id, regatta_id=regatta.id,
                placement=row.placement, scoring_code=row.scoring_code,
                fleet_size=row.fleet_size or size, boat_type=row.boat_type,
                role='crew', points_scored=row.points_scored, division=row.division,
                team_name=row.team_name, crew_partner=person['crew_partner'],
                raw_row_data=row.raw_row_data))

    return missing_raw


def reparse_all(dry_run=True, requeue_missing=False, regatta_id=None):
    report = defaultdict(int)
    query = Regatta.query
    if regatta_id:
        query = query.filter(Regatta.id == regatta_id)
    requeue = []
    for regatta in query.order_by(Regatta.start_date.desc()).all():
        if reparse_regatta(regatta, dry_run, report) and regatta.external_id:
            requeue.append(regatta.external_id)
        if not dry_run:
            db.session.commit()

    if requeue_missing and requeue:
        for check in ClubspotRegattaCheck.query.filter(
                ClubspotRegattaCheck.external_id.in_(requeue)).all():
            report['requeued'] += 1
            if not dry_run:
                check.last_checked_at = None
                check.result_count = 0
        if not dry_run:
            db.session.commit()
    if dry_run:
        db.session.rollback()
    for key in ('regattas', 'rows', 'fleet_sizes', 'scoring_codes', 'crew_added',
                'missing_raw', 'requeued', 'sailors_added'):
        report.setdefault(key, 0)
    return dict(report)
