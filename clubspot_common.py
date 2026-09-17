"""
ClubSpot regatta selection and results-row parsing, shared by the server
scraper (scraper.py) and the local GUI (local_scraper/clubspot_gui.py).

One copy on purpose: the two used to carry independent duplicates of this
logic and drifted apart. Depends only on `requests` so the GUI can import
it without Flask or a database.
"""
import re
from datetime import datetime, timezone

import requests

PARSE_API_URL = 'https://theclubspot.com/parse/classes/regattas'

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

# Clubs whose regattas are never scraped
EXCLUDED_CLUB_IDS = ['HCyTbbCF4n', 'XVgOrNASDY', 'ecNpKgrusD',
                     'GTKaJKeque', 'TTBnsppUug', 'pnBFlwJ2Mf']

REGATTA_KEYS = 'objectId,name,startDate,endDate,clubObject.id,clubObject.name'


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
    return {'__type': 'Date', 'iso': dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')}


# ---------------------------------------------------------------------------
# Regatta list
# ---------------------------------------------------------------------------

def build_regatta_where(start_year=None, before=None):
    """
    The Parse `where` clause for the regatta list.

    before: only regattas whose startDate is at or before this datetime.
    Newest-first ordering would otherwise return UPCOMING regattas, which
    have no results yet.
    """
    where = {
        'archived': {'$ne': True},
        'public': True,
        'clubObject': {'$nin': list(EXCLUDED_CLUB_IDS)},
    }
    start_filter = {}
    if start_year:
        start_filter['$gte'] = {'__type': 'Date', 'iso': f"{start_year}-01-01T00:00:00.000Z"}
    if before is not None:
        start_filter['$lte'] = to_parse_date(before)
    if start_filter:
        where['startDate'] = start_filter
    return where


def fetch_regattas(start_year=None, limit=None, past_only=True, now=None, timeout=60):
    """
    Newest-first regatta list from the Parse API.

    Returns (regattas, total_available). Raises requests exceptions on
    network or HTTP failure; callers decide how to report them.
    """
    now = now or datetime.now(timezone.utc)
    data = dict(
        PARSE_CREDENTIALS,
        where=build_regatta_where(start_year, before=now if past_only else None),
        include='clubObject',
        keys=REGATTA_KEYS,
        count=1,
        limit=limit or 15000,
        order='-startDate',
    )
    response = requests.post(PARSE_API_URL, headers=PARSE_HEADERS, json=data, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return payload.get('results', []), payload.get('count', 0)


def fetch_regatta_by_id(regatta_id, timeout=60):
    """One regatta's metadata; None if not found"""
    data = dict(PARSE_CREDENTIALS, where={'objectId': regatta_id},
                keys='objectId,name,startDate,endDate', limit=1)
    response = requests.post(PARSE_API_URL, headers=PARSE_HEADERS, json=data, timeout=timeout)
    response.raise_for_status()
    results = response.json().get('results', [])
    return results[0] if results else None


def split_by_completion(regattas, now=None):
    """
    Sort a regatta list into (finished, in_progress, future) by its own
    dates, independent of what the API's filter did:

    - future:      startDate after now (the API bound should already have
                   excluded these; any that appear mean it was not honored)
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
# Results page parsing
# ---------------------------------------------------------------------------

# Runs in the results page; returns every data row as pipe-joined cell text,
# across the three table technologies ClubSpot has used
HARVEST_JS = r"""
const out = new Set();

// 1) Classic tables: only rows in <tbody> that have at least one <td>
document.querySelectorAll("table").forEach(tbl => {
    tbl.querySelectorAll("tbody tr").forEach(tr => {
        const tds = Array.from(tr.querySelectorAll("td"));
        if (tds.length === 0) return;
        const parts = tds.map(td => (td.innerText || td.textContent || "").trim()).filter(Boolean);
        const line = parts.join(" | ").trim();
        if (line) out.add(line);
    });
});

// 2) WAI-ARIA grids
document.querySelectorAll("[role='row']").forEach(row => {
    const cells = Array.from(row.querySelectorAll("[role='gridcell'], [role='cell']"));
    if (cells.length === 0) return;
    const parts = cells.map(c => (c.innerText || c.textContent || "").trim()).filter(Boolean);
    const line = parts.join(" | ").trim();
    if (line) out.add(line);
});

// 3) AG Grid
document.querySelectorAll(".ag-row").forEach(row => {
    const cells = Array.from(row.querySelectorAll(".ag-cell"));
    if (cells.length === 0) return;
    const parts = cells.map(c => (c.innerText || c.textContent || "").trim()).filter(Boolean);
    const line = parts.join(" | ").trim();
    if (line) out.add(line);
});

return Array.from(out);
"""


def extract_placement(text):
    """Numeric placement from text like '3', '3rd', '12.'"""
    text = text.replace('st', '').replace('nd', '').replace('rd', '').replace('th', '')
    match = re.search(r'(\d+)', text)
    return int(match.group(1)) if match else None


def parse_result_row(row_text):
    """
    Interpret one pipe-separated results row, e.g. "1 | 12345 | John Doe | 15.0".

    Returns {'placement', 'sailor_name', 'raw_row_data', 'points_scored'?}
    or None when the row is not a result (header rows, blank rows).
    """
    parts = [p.strip() for p in row_text.split('|')]
    if len(parts) < 2:
        return None

    placement = extract_placement(parts[0])
    if not placement:
        return None

    # Sailor name: first non-numeric column after the placement
    sailor_name = None
    for part in parts[1:5]:
        if part and len(part) > 2 and not part.replace('.', '').isdigit():
            sailor_name = part.split('\n')[0].strip()
            break
    if not sailor_name:
        return None

    result = {
        'placement': placement,
        'sailor_name': sailor_name,
        'raw_row_data': row_text,
    }

    # Points usually sit in the last column
    for part in reversed(parts[-3:]):
        try:
            result['points_scored'] = float(part)
            break
        except ValueError:
            continue

    return result
