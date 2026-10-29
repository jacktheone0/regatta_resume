"""
ClubSpot regatta enumeration and results-table parsing, shared by the server
scraper (scraper.py), the catalogue updater (clubspot_catalog.py) and the
local GUI (local_scraper/clubspot_gui.py).

One copy on purpose: the server and the GUI used to carry independent
duplicates of this logic and drifted apart. Depends only on `requests` so
the GUI can import it without Flask or a database.

Enumeration never uses Parse's `skip`: the catalogue is walked in date
windows, each a small independent request, and a window that comes back
full is split in half until none does.
"""
import email.utils
import re
import time
from datetime import datetime, timedelta, timezone

import requests

PARSE_API_URL = 'https://theclubspot.com/parse/classes/regattas'
SITEMAP_URL = 'https://theclubspot.com/sitemap.xml'

PARSE_HEADERS = {
    'Content-Type': 'text/plain',
    'Origin': 'https://theclubspot.com',
    'Referer': 'https://theclubspot.com/events',
    'User-Agent': 'Mozilla/5.0',
}

PARSE_CREDENTIALS = {
    '_method': 'GET',
    '_ApplicationId': 'myclubspot2017',
    '_ClientVersion': 'js4.3.1-forked-1.0',
    '_InstallationId': 'ce500aaa-c2a0-4d06-a9e3-1a558a606542',
}

# Clubs whose regattas are never scraped (ClubSpot's own test clubs)
EXCLUDED_CLUB_IDS = ['HCyTbbCF4n', 'XVgOrNASDY', 'ecNpKgrusD',
                     'GTKaJKeque', 'TTBnsppUug', 'pnBFlwJ2Mf']

# Listing payload: no `include` -- the club name was the only thing it was
# used for and it roughly tripled the response size. The per-regatta scrape
# fetches it when it actually stores a regatta (see fetch_regatta_by_id).
LISTING_KEYS = 'objectId,name,startDate,endDate,createdAt'

# Parse Server's conventional maximum rows per query; a request for more is
# silently truncated to this
PAGE_SIZE = 1000


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def parse_parse_date(value):
    """
    A Parse date ({'__type': 'Date', 'iso': '...'}) or bare ISO string to a
    timezone-aware UTC datetime; None when absent or unreadable.
    """
    if isinstance(value, dict):
        value = value.get('iso')
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_parse_date(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return {'__type': 'Date', 'iso': dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')}


def quarter_start(dt):
    """Midnight UTC on the first day of dt's calendar quarter"""
    dt = dt.astimezone(timezone.utc)
    month = ((dt.month - 1) // 3) * 3 + 1
    return datetime(dt.year, month, 1, tzinfo=timezone.utc)


def iter_quarters_desc(top, floor):
    """
    (start, end) calendar-quarter windows walking backward from top to
    floor; the first and last windows are clipped to the bounds.
    """
    end = top
    while end > floor:
        start = quarter_start(end - timedelta(microseconds=1))
        if start < floor:
            start = floor
        yield start, end
        end = start


def parse_retry_after(value):
    """Retry-After header -> seconds to wait (None when absent/unreadable)"""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def regatta_status(start_date, end_date, today, max_in_progress_days=120):
    """
    'upcoming' | 'in_progress' | 'final' from a regatta's dates.

    A regatta is in progress when it has started and its end date is still
    ahead -- a series posting standings week by week. ClubSpot end dates are
    not always real (placeholder years have been seen), so a regatta that
    started more than max_in_progress_days ago is treated as final whatever
    its end date says.
    """
    if start_date and start_date > today:
        return 'upcoming'
    if end_date and end_date > today:
        if start_date is None or (today - start_date).days <= max_in_progress_days:
            return 'in_progress'
    return 'final'


# ---------------------------------------------------------------------------
# Polite Parse client
# ---------------------------------------------------------------------------

class RateLimitExhausted(Exception):
    """Retries on 429/5xx ran out; the caller should stop enumerating, not crash"""


class ParseClient:
    """
    Sends Parse queries with a pause between requests, honors Retry-After on
    429, and backs off exponentially on 429/5xx/connection errors. Counts
    what it sent so a run can budget requests.
    """

    RETRYABLE_STATUSES = (429, 500, 502, 503, 504)

    def __init__(self, delay_sec=0.5, max_attempts=6, timeout=60, log=None, sleep=time.sleep):
        self.delay_sec = delay_sec
        self.max_attempts = max_attempts
        self.timeout = timeout
        self.log = log or (lambda message: None)
        self.sleep = sleep
        self.requests_made = 0
        self.retries = 0
        self._last_request_at = None

    def _pace(self):
        if self._last_request_at is not None and self.delay_sec:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.delay_sec:
                self.sleep(self.delay_sec - elapsed)
        self._last_request_at = time.monotonic()

    def query(self, where, limit=PAGE_SIZE, order=None, keys=LISTING_KEYS,
              count=False, include=None):
        """One Parse query; returns the decoded JSON payload."""
        data = dict(PARSE_CREDENTIALS, where=where, limit=limit, keys=keys)
        if order:
            data['order'] = order
        if count:
            data['count'] = 1
        if include:
            data['include'] = include

        attempt = 0
        backoff = 2.0
        while True:
            self._pace()
            status = None
            response = None
            try:
                response = requests.post(PARSE_API_URL, headers=PARSE_HEADERS, json=data,
                                         timeout=self.timeout)
                self.requests_made += 1
                status = response.status_code
                if status < 400:
                    return response.json()
                if status not in self.RETRYABLE_STATUSES:
                    response.raise_for_status()
            except requests.RequestException as e:
                if response is not None and status is not None and status not in self.RETRYABLE_STATUSES:
                    raise
                self.log(f"Parse request failed ({e.__class__.__name__}: {e})")

            attempt += 1
            self.retries += 1
            if attempt >= self.max_attempts:
                raise RateLimitExhausted(
                    f"gave up after {attempt} attempts (last status {status or 'no response'})")

            wait = backoff
            if status == 429 and response is not None:
                retry_after = parse_retry_after(response.headers.get('Retry-After'))
                if retry_after is not None:
                    wait = max(retry_after, backoff)
            self.log(f"HTTP {status or 'error'} from Parse; waiting {wait:.0f}s "
                     f"(attempt {attempt}/{self.max_attempts})")
            self.sleep(wait)
            backoff = min(backoff * 2, 120.0)


# ---------------------------------------------------------------------------
# Regatta list
# ---------------------------------------------------------------------------

def build_regatta_where(field='startDate', start=None, end=None, include_archived=False):
    """
    Parse `where` for the regatta list: public regattas outside the excluded
    clubs, with `field` in [start, end). The upper bound is exclusive so
    split windows never overlap. The archived filter is kept unless the
    caller has measured that it hides real history (see count_regattas).
    """
    where = {
        'public': True,
        'clubObject': {'$nin': list(EXCLUDED_CLUB_IDS)},
    }
    if not include_archived:
        where['archived'] = {'$ne': True}
    window = {}
    if start is not None:
        window['$gte'] = to_parse_date(start)
    if end is not None:
        window['$lt'] = to_parse_date(end)
    if window:
        where[field] = window
    return where


def count_regattas(client, where):
    """How many regattas match `where`, without fetching any"""
    return int(client.query(where, limit=0, count=True).get('count', 0) or 0)


def measure_filters(client):
    """
    Step 0: how many regattas each filter hides. Four count queries:
    everything, public only, public and not archived (what the scraper
    uses), and archived. The excluded-clubs clause is deliberately left
    out: a `$nin` on the club pointer makes the server's count crawl, and
    those six clubs are a rounding error in these totals.
    """
    return {
        'all': count_regattas(client, {}),
        'public': count_regattas(client, {'public': True}),
        'public_not_archived': count_regattas(client, {'public': True, 'archived': {'$ne': True}}),
        'archived': count_regattas(client, {'archived': True}),
    }


def fetch_window(client, start, end, field='startDate', include_archived=False,
                 min_span=timedelta(hours=1), page_size=PAGE_SIZE):
    """
    Every regatta with `field` in [start, end), never using skip.

    A window that comes back full (page_size rows) is split in half and
    both halves re-queried, newest half first, until no window is full or a
    window is already min_span wide (then its rows are kept and it is
    counted as an overflow). Returns (rows, requests_used, overflow_windows).
    """
    rows = []
    requests_used = 0
    overflow = 0
    stack = [(start, end)]
    while stack:
        window_start, window_end = stack.pop()
        where = build_regatta_where(field, window_start, window_end, include_archived)
        payload = client.query(where, limit=page_size, order=f'-{field},objectId')
        requests_used += 1
        page = payload.get('results', [])
        if len(page) >= page_size and (window_end - window_start) > min_span:
            middle = window_start + (window_end - window_start) / 2
            stack.append((window_start, middle))
            stack.append((middle, window_end))  # popped first: newest half first
            continue
        if len(page) >= page_size:
            overflow += 1
        rows.extend(page)
    return rows, requests_used, overflow


def fetch_regattas(start_year=None, limit=None, past_only=True, now=None,
                   timeout=60, progress=None, include_archived=False):
    """
    The newest regattas in ONE request (at most PAGE_SIZE), for the GUI's
    quick tests. Full enumeration lives in fetch_window / clubspot_catalog.

    Returns (regattas, total_matching).
    """
    now = now or datetime.now(timezone.utc)
    start = datetime(start_year, 1, 1, tzinfo=timezone.utc) if start_year else None
    where = build_regatta_where('startDate', start, now if past_only else None, include_archived)
    client = ParseClient(delay_sec=0, timeout=timeout)
    payload = client.query(where, limit=min(limit or PAGE_SIZE, PAGE_SIZE),
                           order='-startDate,objectId', count=True)
    regattas = payload.get('results', [])
    total = int(payload.get('count', 0) or 0)
    if progress:
        progress(f"Regatta list: {len(regattas)} rows ({total} matching)")
    return regattas, total


def fetch_regatta_by_id(regatta_id, timeout=60, include_club=False, client=None):
    """One regatta's metadata (optionally with its club); None if not found"""
    client = client or ParseClient(delay_sec=0, timeout=timeout)
    keys = LISTING_KEYS + (',clubObject.name' if include_club else '')
    payload = client.query({'objectId': regatta_id}, limit=1, keys=keys,
                           include='clubObject' if include_club else None)
    results = payload.get('results', [])
    return results[0] if results else None


def split_by_completion(regattas, now=None):
    """
    Sort a regatta list into (finished, in_progress, future) by its own
    dates, independent of what the API's filter did:

    - future:      startDate after now
    - in_progress: started, but endDate is still ahead -- results are
                   partial or unposted, so they are worth a later pass
    - finished:    everything else, including regattas with no endDate
    """
    now = now or datetime.now(timezone.utc)
    finished, in_progress, future = [], [], []
    for regatta in regattas:
        start = parse_parse_date(regatta.get('startDate'))
        end = parse_parse_date(regatta.get('endDate'))
        if start is not None and start > now:
            future.append(regatta)
        elif end is not None and end > now:
            in_progress.append(regatta)
        else:
            finished.append(regatta)
    return finished, in_progress, future


# ---------------------------------------------------------------------------
# Sitemap: a second source of regatta IDs that does not touch the API
# ---------------------------------------------------------------------------

_REGATTA_ID_RE = re.compile(r'/regatta/([A-Za-z0-9]{6,20})')
_LOC_RE = re.compile(r'<loc>\s*([^<]+?)\s*</loc>', re.IGNORECASE)


def sitemap_regatta_ids(timeout=20, max_children=50):
    """
    Regatta IDs listed in theclubspot.com/sitemap.xml and any child sitemaps
    it indexes. Returns a dict: found, status, ids (set), fetched (requests),
    children (child sitemaps seen). Never raises.
    """
    headers = {'User-Agent': PARSE_HEADERS['User-Agent']}
    report = {'found': False, 'status': None, 'ids': set(), 'fetched': 0, 'children': 0}
    try:
        response = requests.get(SITEMAP_URL, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        report['error'] = str(e)
        return report
    report['fetched'] = 1
    report['status'] = response.status_code
    if response.status_code != 200:
        return report
    report['found'] = True
    text = response.text
    report['ids'].update(_REGATTA_ID_RE.findall(text))

    children = [u for u in _LOC_RE.findall(text)
                if u.lower().endswith('.xml') or 'sitemap' in u.lower()]
    report['children'] = len(children)
    for child in children[:max_children]:
        try:
            child_response = requests.get(child, headers=headers, timeout=timeout)
        except requests.RequestException:
            continue
        report['fetched'] += 1
        if child_response.status_code == 200:
            report['ids'].update(_REGATTA_ID_RE.findall(child_response.text))
    return report


# ---------------------------------------------------------------------------
# Results page harvesting
# ---------------------------------------------------------------------------

# Runs in the results page. Returns every table it can find as
# {headers: [...], rows: [[cell, ...], ...]} across the three table
# technologies ClubSpot has used. Cells keep their position (no filtering of
# blanks) so columns can be mapped by header; multi-line cells keep their
# newlines, which is how a skipper and crew share one cell.
HARVEST_JS = r"""
const tables = [];
const text = el => (el.innerText || el.textContent || "").trim();

// 1) Classic tables
document.querySelectorAll("table").forEach(tbl => {
    const headers = Array.from(tbl.querySelectorAll("thead th, thead td")).map(text);
    const rows = [];
    tbl.querySelectorAll("tbody tr").forEach(tr => {
        const tds = Array.from(tr.querySelectorAll("td"));
        if (tds.length === 0) return;
        const cells = tds.map(text);
        if (cells.some(Boolean)) rows.push(cells);
    });
    if (rows.length) tables.push({headers: headers, rows: rows});
});

// 2) WAI-ARIA grids
document.querySelectorAll("[role='grid'], [role='table']").forEach(grid => {
    const headers = Array.from(grid.querySelectorAll("[role='columnheader']")).map(text);
    const rows = [];
    grid.querySelectorAll("[role='row']").forEach(row => {
        const cells = Array.from(row.querySelectorAll("[role='gridcell'], [role='cell']")).map(text);
        if (cells.length && cells.some(Boolean)) rows.push(cells);
    });
    if (rows.length) tables.push({headers: headers, rows: rows});
});

// 3) AG Grid
document.querySelectorAll(".ag-root-wrapper, .ag-root").forEach(ag => {
    const headers = Array.from(ag.querySelectorAll(".ag-header-cell-text")).map(text);
    const rows = [];
    ag.querySelectorAll(".ag-row").forEach(row => {
        const cells = Array.from(row.querySelectorAll(".ag-cell")).map(text);
        if (cells.length && cells.some(Boolean)) rows.push(cells);
    });
    if (rows.length) tables.push({headers: headers, rows: rows});
});

return tables;
"""

# Header words that identify each column (matched as substrings, lowercased)
PLACE_HEADERS = ('pos', 'place', 'rank', 'finish', 'overall', 'result')
SKIPPER_HEADERS = ('skipper', 'helm', 'sailor', 'name', 'owner', 'competitor', 'entrant', 'driver')
SKIPPER_HEADER_EXCLUDE = ('boat', 'sail', 'club', 'team', 'yacht', 'crew', 'class', 'fleet')
CREW_HEADERS = ('crew',)
SAIL_HEADERS = ('sail', 'bow', 'hull')
BOAT_HEADERS = ('boat', 'yacht')
POINTS_HEADERS = ('points', 'pts', 'total', 'score', 'net')
DIVISION_HEADERS = ('division', 'div')
CLASS_HEADERS = ('class', 'fleet')
TEAM_HEADERS = ('club', 'team', 'school')

# Scoring abbreviations that replace a finishing place
SCORING_CODES = {'dnf', 'dns', 'dsq', 'dnc', 'ocs', 'ret', 'raf', 'ufd', 'bfd',
                 'dne', 'zfp', 'scp', 'dpi', 'nsc', 'dgm', 'dp', 'rdg', 'dnq'}

_NAME_SPLIT_RE = re.compile(r'\s*(?:\n|/|&|\+|;|\band\b)\s*')
_NON_PERSON = {'tbd', 'tba', 'n/a', 'na', 'none', '-', '--', 'unknown', 'crew', 'skipper'}


def _find_header(headers, wanted, exclude=()):
    for index, header in enumerate(headers):
        lowered = (header or '').lower()
        if any(word in lowered for word in exclude):
            continue
        if any(word in lowered for word in wanted):
            return index
    return None


def _column_map(headers):
    """Column indices by role, from a header row; None where not found."""
    columns = {
        'place': _find_header(headers, PLACE_HEADERS),
        'skipper': _find_header(headers, SKIPPER_HEADERS, exclude=SKIPPER_HEADER_EXCLUDE),
        'crew': _find_header(headers, CREW_HEADERS),
        'sail': _find_header(headers, SAIL_HEADERS),
        'boat': _find_header(headers, BOAT_HEADERS, exclude=('class', 'type')),
        'points': _find_header(headers, POINTS_HEADERS),
        'division': _find_header(headers, DIVISION_HEADERS),
        'class': _find_header(headers, CLASS_HEADERS),
        'team': _find_header(headers, TEAM_HEADERS),
    }
    # A header row is only trusted if it locates the two things a result
    # needs; otherwise the positional heuristic is safer
    if columns['place'] is None or columns['skipper'] is None:
        return None
    return columns


def looks_like_person(text):
    """A cell fragment that could be a person's name (not a number, code or blank)"""
    stripped = (text or '').strip()
    if len(stripped) < 3:
        return False
    if stripped.replace('.', '').replace(' ', '').replace('-', '').isdigit():
        return False
    if stripped.lower() in _NON_PERSON or stripped.lower() in SCORING_CODES:
        return False
    return True


def split_names(text):
    """
    The people in one cell: 'Jane Doe\nJim Roe', 'Jane Doe / Jim Roe',
    'Jane Doe & Jim Roe'. A single 'Last, First' is one person, reordered.
    """
    text = (text or '').strip()
    if not text:
        return []
    parts = [p.strip() for p in _NAME_SPLIT_RE.split(text) if p.strip()]
    names = []
    for part in parts:
        if ',' in part:
            pieces = [p.strip() for p in part.split(',') if p.strip()]
            if len(pieces) == 2 and ' ' not in pieces[0] and ' ' not in pieces[1]:
                names.append(f"{pieces[1]} {pieces[0]}")   # Last, First -> First Last
                continue
            names.extend(pieces)
        else:
            names.append(part)
    return [n for n in names if looks_like_person(n)]


def extract_placement(text):
    """Numeric placement from text like '3', '3rd', '12.'; None for codes/blank"""
    if not text:
        return None
    if text.strip().lower() in SCORING_CODES:
        return None
    cleaned = text.replace('st', '').replace('nd', '').replace('rd', '').replace('th', '')
    match = re.search(r'(\d+)', cleaned)
    return int(match.group(1)) if match else None


def scoring_code(text):
    lowered = (text or '').strip().lower()
    return lowered.upper() if lowered in SCORING_CODES else None


def _points(text):
    try:
        return float((text or '').strip())
    except ValueError:
        return None


def _people_results(cells, place_text, skipper_text, crew_text, extras):
    """One result dict per person on the boat, skipper first."""
    placement = extract_placement(place_text)
    code = scoring_code(place_text)
    if placement is None and code is None:
        return []
    skippers = split_names(skipper_text)
    crew = split_names(crew_text) if crew_text else []
    if not crew and len(skippers) > 1:
        # No crew column: the skipper cell carries everyone, skipper first
        skippers, crew = skippers[:1], skippers[1:]
    if not skippers and not crew:
        return []
    everyone = skippers[:1] + crew
    raw = ' | '.join(c for c in cells)
    results = []
    for index, name in enumerate(everyone):
        role = 'skipper' if index == 0 and skippers else 'crew'
        partners = [n for n in everyone if n != name]
        results.append(dict(extras,
                            placement=placement,
                            scoring_code=code,
                            sailor_name=name,
                            role=role,
                            crew_partner=', '.join(partners) or None,
                            raw_row_data=raw))
    return results


def _parse_by_headers(cells, columns):
    def cell(key):
        index = columns.get(key)
        return cells[index] if index is not None and index < len(cells) else ''

    extras = {
        'points_scored': _points(cell('points')) if columns.get('points') is not None else None,
        'division': cell('division').strip() or None,
        'boat_type': cell('class').strip() or None,
        'team_name': cell('team').strip() or None,
        'sail_number': cell('sail').strip() or None,
        'boat_name': cell('boat').strip() or None,
    }
    return _people_results(cells, cell('place'), cell('skipper'), cell('crew'), extras)


def _parse_by_position(cells):
    """
    No usable header row: the first cell is the place, the first following
    cell that reads like a name holds the people, and points are the last
    numeric cell. The historical heuristic, now returning everyone aboard.
    """
    parts = [c.strip() for c in cells if c and c.strip()]
    if len(parts) < 2:
        return []
    place_text = parts[0]
    if extract_placement(place_text) is None and scoring_code(place_text) is None:
        return []
    name_text = None
    for part in parts[1:5]:
        if looks_like_person(part.split('\n')[0]):
            name_text = part
            break
    if not name_text:
        return []
    points = None
    for part in reversed(parts[-3:]):
        value = _points(part)
        if value is not None:
            points = value
            break
    extras = {'points_scored': points, 'division': None, 'boat_type': None,
              'team_name': None, 'sail_number': None, 'boat_name': None}
    return _people_results(parts, place_text, name_text, '', extras)


def parse_results_tables(tables):
    """
    Harvested tables -> one result dict per person per boat, skipper first:
    {placement, scoring_code, sailor_name, role, crew_partner, points_scored,
     division, boat_type, team_name, sail_number, boat_name, raw_row_data}.

    Columns are mapped by header when the table has one that names a place
    and a skipper column; otherwise the positional heuristic is used.
    Identical rows (the same boat rendered twice) are parsed once.
    """
    results = []
    seen = set()
    for table in tables or []:
        headers = [h or '' for h in (table.get('headers') or [])]
        columns = _column_map(headers) if headers else None
        for cells in table.get('rows') or []:
            cells = [c if c is not None else '' for c in cells]
            key = tuple(cells)
            if key in seen:
                continue
            seen.add(key)
            parsed = _parse_by_headers(cells, columns) if columns else _parse_by_position(cells)
            results.extend(parsed)
    return results


def parse_result_row(row_text):
    """
    Interpret one pipe-separated results row, e.g. "1 | 12345 | John Doe | 15.0",
    returning the SKIPPER's result only (compatibility shim over the
    positional parser). None when the row is not a result.
    """
    people = _parse_by_position([p.strip() for p in row_text.split('|')])
    return people[0] if people else None


def count_boats(results):
    """Boats per division (one boat per raw row), for fleet sizes"""
    boats = {}
    for result in results:
        boats.setdefault(result.get('division'), set()).add(result.get('raw_row_data'))
    return {division: len(rows) for division, rows in boats.items()}


def attach_fleet_sizes(results):
    """Set fleet_size on every result from the boat count of its division"""
    sizes = count_boats(results)
    for result in results:
        result['fleet_size'] = sizes.get(result.get('division'))
    return results
