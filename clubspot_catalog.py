"""
The ClubSpot regatta catalogue: every regatta ID ever seen, kept in
clubspot_regatta_checks, and the two-frontier walk that keeps it current.

Enumeration is separate from scraping. Each run:

  1. measures the API's filters (step 0 counts) and reads the sitemap,
  2. walks date windows -- forward catch-up first (present day, plus a
     forward window for upcoming regattas, back to the newest window
     already enumerated), then historical backfill (from the oldest
     enumerated window further into the past) -- upserting every regatta
     it sees, and advancing the two markers after every completed window,
  3. hands the scraper the catalogue rows that are due.

A truncated or throttled listing on one day never loses IDs found on an
earlier day, and the full historical walk is a one-time backfill rather
than something every run depends on.
"""
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

from models import db, ClubspotRegattaCheck, Regatta, Result, ScraperState
from clubspot_common import (ParseClient, RateLimitExhausted, fetch_window,
                             iter_quarters_desc, measure_filters, parse_parse_date,
                             sitemap_regatta_ids)

# State keys (scraper_state table)
KEY_NEWEST = 'clubspot_enum_newest'              # upper bound of the enumerated range
KEY_OLDEST = 'clubspot_enum_oldest'              # lower bound of the enumerated range
KEY_FORWARD_CURSOR = 'clubspot_enum_forward_cursor'  # in-progress forward walk, resume point
KEY_LAST_RUN_AT = 'clubspot_enum_last_run_at'
KEY_BACKFILL_DONE = 'clubspot_enum_backfill_complete'
KEY_LAST_SUMMARY = 'clubspot_last_enum_summary'
KEY_FILTER_COUNTS = 'clubspot_filter_counts'
KEY_SITEMAP = 'clubspot_sitemap'


def config():
    """Run limits and walk parameters, all overridable from the environment"""
    env = os.environ.get
    return {
        'request_budget': int(env('SCRAPER_ENUM_REQUEST_BUDGET', '300')),
        'time_budget_min': float(env('SCRAPER_ENUM_TIME_BUDGET_MIN', '20')),
        'delay_sec': float(env('SCRAPER_ENUM_DELAY_SEC', '0.5')),
        'forward_days': int(env('SCRAPER_FORWARD_WINDOW_DAYS', '180')),
        'recent_rescan_days': int(env('SCRAPER_RECENT_RESCAN_DAYS', '60')),
        'floor': datetime.fromisoformat(env('SCRAPER_ENUM_FLOOR_DATE', '2016-01-01')).replace(tzinfo=timezone.utc),
        'empty_quarters_done': int(env('SCRAPER_ENUM_EMPTY_QUARTERS_DONE', '8')),
        'include_archived': env('SCRAPER_INCLUDE_ARCHIVED', 'false').lower() == 'true',
    }


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def get_state(key, default=None):
    row = db.session.get(ScraperState, key)
    return row.value if row else default


def set_state(key, value):
    row = db.session.get(ScraperState, key)
    if row is None:
        row = ScraperState(key=key)
        db.session.add(row)
    row.value = None if value is None else str(value)
    row.updated_at = datetime.utcnow()
    db.session.commit()


def get_state_datetime(key):
    value = get_state(key)
    return datetime.fromisoformat(value) if value else None


def get_state_json(key):
    value = get_state(key)
    return json.loads(value) if value else None


# ---------------------------------------------------------------------------
# Catalogue upserts
# ---------------------------------------------------------------------------

def upsert_regattas(rows, source='api', seen_at=None):
    """
    Add or refresh catalogue rows from API listing rows (dicts with
    objectId, name, startDate, endDate, createdAt). Returns (added, updated).
    """
    seen_at = seen_at or datetime.utcnow()
    added = updated = 0
    by_id = {r['objectId']: r for r in rows if r.get('objectId')}
    if not by_id:
        return 0, 0
    ids = list(by_id)
    existing = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for check in ClubspotRegattaCheck.query.filter(ClubspotRegattaCheck.external_id.in_(chunk)).all():
            existing[check.external_id] = check

    for regatta_id, row in by_id.items():
        start = parse_parse_date(row.get('startDate'))
        end = parse_parse_date(row.get('endDate'))
        created = parse_parse_date(row.get('createdAt'))
        check = existing.get(regatta_id)
        if check is None:
            check = ClubspotRegattaCheck(external_id=regatta_id, first_seen_at=seen_at,
                                         source=source, result_count=0, check_count=0)
            db.session.add(check)
            added += 1
        else:
            updated += 1
        check.last_seen_at = seen_at
        if row.get('name'):
            check.name = row['name']
        if start:
            check.start_date = start.date()
        if end:
            check.end_date = end.date()
        if created:
            check.remote_created_at = created.replace(tzinfo=None)
    db.session.commit()
    return added, updated


def upsert_ids(ids, source='sitemap', seen_at=None):
    """Add bare IDs (no dates yet); existing rows are left as they are."""
    seen_at = seen_at or datetime.utcnow()
    ids = [i for i in set(ids) if i]
    added = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        known = {c.external_id for c in ClubspotRegattaCheck.query
                 .filter(ClubspotRegattaCheck.external_id.in_(chunk)).all()}
        for regatta_id in chunk:
            if regatta_id in known:
                continue
            db.session.add(ClubspotRegattaCheck(external_id=regatta_id, first_seen_at=seen_at,
                                                last_seen_at=seen_at, source=source,
                                                result_count=0, check_count=0))
            added += 1
        db.session.commit()
    return added


# ---------------------------------------------------------------------------
# Step 0 and step 4 measurements
# ---------------------------------------------------------------------------

def record_filter_counts(client, log):
    """Step 0: count regattas with and without the filters; store and log."""
    try:
        counts = measure_filters(client)
    except Exception as e:
        log(f"Filter counts unavailable: {e}")
        return None
    counts['measured_at'] = datetime.utcnow().isoformat()
    set_state(KEY_FILTER_COUNTS, json.dumps(counts))
    hidden_by_archived = counts['public'] - counts['public_not_archived']
    log(f"Filter counts: all={counts['all']}, public={counts['public']}, "
        f"public+not-archived (what the scraper uses)={counts['public_not_archived']}, "
        f"archived={counts['archived']}; the archived filter hides {hidden_by_archived}")
    return counts


def record_sitemap(log):
    """Step 4: read the sitemap, merge any regatta IDs it lists."""
    report = sitemap_regatta_ids()
    added = 0
    if report['ids']:
        added = upsert_ids(report['ids'], source='sitemap')
    stored = {k: v for k, v in report.items() if k != 'ids'}
    stored['regatta_ids'] = len(report['ids'])
    stored['added_to_catalog'] = added
    stored['checked_at'] = datetime.utcnow().isoformat()
    set_state(KEY_SITEMAP, json.dumps(stored))
    if report['found']:
        log(f"Sitemap: found (HTTP {report['status']}), {report['children']} child sitemaps, "
            f"{len(report['ids'])} regatta IDs, {added} new to the catalogue")
    else:
        log(f"Sitemap: not available (HTTP {report['status']}{', ' + report['error'] if report.get('error') else ''})")
    return stored


# ---------------------------------------------------------------------------
# The two-frontier walk
# ---------------------------------------------------------------------------

class Budget:
    def __init__(self, requests_allowed, seconds_allowed, client):
        self.requests_allowed = requests_allowed
        self.seconds_allowed = seconds_allowed
        self.client = client
        self.started = time.monotonic()

    def exhausted(self):
        if self.requests_allowed and self.client.requests_made >= self.requests_allowed:
            return f"request budget ({self.requests_allowed}) used"
        if self.seconds_allowed and time.monotonic() - self.started >= self.seconds_allowed:
            return f"enumeration time budget ({self.seconds_allowed / 60:.0f} min) used"
        return None


def update_catalog(log, now=None):
    """
    Bring the catalogue up to date within this run's budget. Returns a
    summary dict (also stored under KEY_LAST_SUMMARY).
    """
    cfg = config()
    now = now or datetime.now(timezone.utc)
    client = ParseClient(delay_sec=cfg['delay_sec'], log=log)
    budget = Budget(cfg['request_budget'], cfg['time_budget_min'] * 60, client)
    summary = {
        'windows': 0, 'requests': 0, 'added': 0, 'updated': 0, 'overflow_windows': 0,
        'forward_windows': 0, 'backfill_windows': 0, 'created_catchup_rows': 0,
        'stopped': None, 'started_at': now.isoformat(),
    }

    def walk(start, end, label, field='startDate'):
        rows, used, overflow = fetch_window(client, start, end, field=field,
                                            include_archived=cfg['include_archived'])
        added, updated = upsert_regattas(rows)
        summary['windows'] += 1
        summary['requests'] = client.requests_made
        summary['added'] += added
        summary['updated'] += updated
        summary['overflow_windows'] += overflow
        log(f"{label} {start.date()} .. {end.date()}: {len(rows)} regattas "
            f"({added} new) in {used} request(s)")
        return rows

    top = now + timedelta(days=cfg['forward_days'])
    newest = get_state_datetime(KEY_NEWEST)
    oldest = get_state_datetime(KEY_OLDEST)
    cursor = get_state_datetime(KEY_FORWARD_CURSOR)
    last_run_at = get_state_datetime(KEY_LAST_RUN_AT)

    try:
        # Step 0 / step 4 happen on every run: four count queries and one
        # sitemap fetch are cheap, and their numbers belong in the summary
        summary['filter_counts'] = record_filter_counts(client, log)
        summary['sitemap'] = record_sitemap(log)

        if newest is None:
            # First run ever: nothing is enumerated yet. Both markers start
            # at the top and the backfill does all the work, newest first.
            newest = oldest = top
            set_state(KEY_NEWEST, newest.isoformat())
            set_state(KEY_OLDEST, oldest.isoformat())
            log(f"First enumeration: markers set to {top.date()}; walking backward from there")

        # (a) Forward catch-up: from the top (present day + forward window)
        # back to the newest enumerated window. The recent band is always
        # re-walked -- regattas can be created or re-dated inside windows
        # that were already enumerated -- and upserts make that harmless.
        lower_target = min(newest, now - timedelta(days=cfg['recent_rescan_days']))
        start_from = cursor if (cursor and cursor > lower_target) else top
        if start_from > lower_target:
            if cursor:
                log(f"Resuming forward catch-up at {cursor.date()}")
            for start, end in iter_quarters_desc(start_from, lower_target):
                stop = budget.exhausted()
                if stop:
                    summary['stopped'] = f"forward catch-up: {stop}"
                    break
                walk(start, end, 'Forward')
                summary['forward_windows'] += 1
                set_state(KEY_FORWARD_CURSOR, start.isoformat())
            else:
                newest = top
                set_state(KEY_NEWEST, newest.isoformat())
                set_state(KEY_FORWARD_CURSOR, None)

        # Anything created since the last run, wherever its startDate falls
        # (a regatta posted today for a window long since enumerated)
        if last_run_at and not summary['stopped']:
            rows = walk(last_run_at - timedelta(days=1), now + timedelta(days=1),
                        'Created since last run', field='createdAt')
            summary['created_catchup_rows'] = len(rows)

        # (b) Historical backfill: from the oldest enumerated window further
        # into the past, until the floor or a long stretch of nothing
        if not summary['stopped'] and get_state(KEY_BACKFILL_DONE) != '1':
            empty_streak = 0
            for start, end in iter_quarters_desc(oldest, cfg['floor']):
                stop = budget.exhausted()
                if stop:
                    summary['stopped'] = f"backfill: {stop}"
                    break
                rows = walk(start, end, 'Backfill')
                summary['backfill_windows'] += 1
                oldest = start
                set_state(KEY_OLDEST, oldest.isoformat())
                empty_streak = empty_streak + 1 if not rows else 0
                if empty_streak >= cfg['empty_quarters_done']:
                    log(f"{empty_streak} consecutive empty quarters before {end.date()}: "
                        f"treating {end.date()} as the earliest regatta on the platform")
                    set_state(KEY_BACKFILL_DONE, '1')
                    break
            else:
                set_state(KEY_BACKFILL_DONE, '1')
                log(f"Backfill reached the floor {cfg['floor'].date()}: historical walk complete")

    except RateLimitExhausted as e:
        summary['stopped'] = f"rate limited: {e}"
        log(f"Enumeration stopped: {e}; markers are saved, the next run resumes here")
    except Exception as e:
        db.session.rollback()
        summary['stopped'] = f"error: {e}"
        log(f"Enumeration stopped on an error: {e}; markers are saved, the next run resumes here")

    summary['requests'] = client.requests_made
    summary['retries'] = client.retries
    summary['newest'] = (get_state(KEY_NEWEST) or '')[:10]
    summary['oldest'] = (get_state(KEY_OLDEST) or '')[:10]
    summary['forward_cursor'] = (get_state(KEY_FORWARD_CURSOR) or '')[:10] or None
    summary['backfill_complete'] = get_state(KEY_BACKFILL_DONE) == '1'
    summary['catalog_size'] = ClubspotRegattaCheck.query.count()
    set_state(KEY_LAST_RUN_AT, now.isoformat())
    set_state(KEY_LAST_SUMMARY, json.dumps(summary, default=str))
    log(f"Enumeration: {summary['windows']} windows, {summary['requests']} requests, "
        f"{summary['added']} new regattas, catalogue {summary['catalog_size']}, "
        f"enumerated range {summary['oldest']} .. {summary['newest']}"
        + (f", stopped: {summary['stopped']}" if summary['stopped'] else ''))
    return summary


# ---------------------------------------------------------------------------
# What the scraper should visit
# ---------------------------------------------------------------------------

def sync_results_into_catalog():
    """
    Regattas scraped before the catalogue existed have results but no
    visit record; mark them visited so they are neither re-scraped nor
    counted as backlog. Returns the number of rows updated.
    """
    counts = dict(
        db.session.query(Regatta.external_id, db.func.count(Result.id))
        .join(Result, Result.regatta_id == Regatta.id)
        .filter(Regatta.external_id.isnot(None))
        .group_by(Regatta.external_id).all()
    )
    if not counts:
        return 0
    updated = 0
    ids = list(counts)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for check in ClubspotRegattaCheck.query.filter(
                ClubspotRegattaCheck.external_id.in_(chunk),
                ClubspotRegattaCheck.result_count == 0).all():
            check.result_count = counts[check.external_id]
            check.last_checked_at = check.last_checked_at or datetime.utcnow()
            updated += 1
    db.session.commit()
    return updated


def select_due(now=None, recheck_window_days=30, recheck_interval_days=2,
               start_year=None, limit=None):
    """
    Catalogue rows the scraper should visit this run, newest first:
    finished regattas (no end date -> started counts as finished) that were
    never visited, plus visited-but-empty ones still inside the re-check
    window and not checked within the interval. Rows without dates
    (sitemap-only) come last so their metadata is fetched when reached.

    Returns (due_rows, counts) where counts describes the whole catalogue.
    """
    now = now or datetime.utcnow()
    today = now.date()
    due_before = now - timedelta(days=recheck_interval_days)
    rows = ClubspotRegattaCheck.query.all()

    with_results = 0
    due = []
    for check in rows:
        if check.result_count and check.result_count > 0:
            with_results += 1
            continue
        if start_year and check.start_date and check.start_date.year < start_year:
            continue
        ended = check.end_date or check.start_date
        if ended and ended > today:
            continue  # upcoming or still running
        if check.last_checked_at is None:
            due.append(check)
            continue
        if ended and (today - ended).days > recheck_window_days:
            continue
        if check.last_checked_at > due_before:
            continue
        due.append(check)

    due.sort(key=lambda c: (c.start_date is None, -(c.start_date.toordinal() if c.start_date else 0)))
    if limit:
        due = due[:limit]
    counts = {'catalog': len(rows), 'with_results': with_results, 'due': len(due)}
    return due, counts
