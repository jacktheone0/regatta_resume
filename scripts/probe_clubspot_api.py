"""
Probe theclubspot.com from your own machine (the scraper's sandbox cannot
reach it). Needs the `requests` package and open internet:

    python scripts/probe_clubspot_api.py            # step 0 counts + sitemap
    python scripts/probe_clubspot_api.py counts     # just the filter counts
    python scripts/probe_clubspot_api.py sitemap    # just the sitemap check
    python scripts/probe_clubspot_api.py sitemap --save regatta_ids.txt
    python scripts/probe_clubspot_api.py results    # look for a results API

Paste the output back into the Claude session. A saved ID file can be
merged into the catalogue with:  flask import-regatta-ids regatta_ids.txt
"""
import json
import os
import sys
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clubspot_common import (PARSE_HEADERS, PARSE_CREDENTIALS, ParseClient,  # noqa: E402
                             build_regatta_where, count_regattas, measure_filters,
                             sitemap_regatta_ids)


def probe_counts():
    """Step 0: does the archived filter hide the missing regattas?"""
    client = ParseClient(delay_sec=0.3, timeout=30, log=print)
    print('Regatta counts on ClubSpot (each line appears as its query returns):', flush=True)
    labels = [
        ('all', {}, 'everything (no filters at all)'),
        ('public', {'public': True}, 'public'),
        ('public_not_archived', {'public': True, 'archived': {'$ne': True}}, 'public AND not archived (scraper filter)'),
        ('archived', {'archived': True}, 'archived'),
    ]
    counts = {}
    for key, where, label in labels:
        counts[key] = count_regattas(client, where)
        print(f"  {label + ':':44} {counts[key]}", flush=True)
    print(f"  -> the archived filter hides {counts['public'] - counts['public_not_archived']} public regattas")
    print(f"  -> {counts['all'] - counts['public']} regattas are not public")
    print()
    print('Per year (public, not archived):', flush=True)
    year = datetime.now(timezone.utc).year
    for y in range(2016, year + 2):
        where = {'public': True, 'archived': {'$ne': True},
                 'startDate': {'$gte': {'__type': 'Date', 'iso': f'{y}-01-01T00:00:00.000Z'},
                               '$lt': {'__type': 'Date', 'iso': f'{y + 1}-01-01T00:00:00.000Z'}}}
        print(f"  {y}: {count_regattas(client, where)}", flush=True)
    return counts


def probe_sitemap(save_path=None):
    """Step 4: is there a sitemap listing regatta URLs?"""
    report = sitemap_regatta_ids()
    if not report['found']:
        print(f"Sitemap: NOT available (HTTP {report['status']}"
              f"{', ' + report['error'] if report.get('error') else ''})")
        return report
    print(f"Sitemap: found (HTTP {report['status']}), {report['children']} child sitemap(s), "
          f"{report['fetched']} file(s) fetched, {len(report['ids'])} regatta IDs")
    if save_path and report['ids']:
        with open(save_path, 'w') as f:
            f.write('\n'.join(sorted(report['ids'])) + '\n')
        print(f"Saved {len(report['ids'])} IDs to {save_path}")
    return report


CANDIDATE_CLASSES = [
    'scores', 'results', 'raceResults', 'race_results', 'finishes',
    'entries', 'registrations', 'boats', 'teams', 'classes', 'divisions',
    'races', 'participants', 'sailors', 'crews', 'standings', 'placements',
]


def probe_results_classes():
    """Is there a Parse class that serves per-sailor results (no Chrome needed)?"""
    def query(cls, where, limit=3, extra=None):
        data = dict(PARSE_CREDENTIALS, where=where, limit=limit)
        if extra:
            data.update(extra)
        resp = requests.post(f'https://theclubspot.com/parse/classes/{cls}', headers=PARSE_HEADERS,
                             json=data, timeout=30)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, {'raw': resp.text[:200]}

    code, payload = query('regattas', {'public': True, 'archived': {'$ne': True}}, limit=1,
                          extra={'order': '-startDate', 'keys': 'objectId,name,startDate'})
    results = payload.get('results') or []
    if not results:
        print('Could not fetch a regatta to probe against; stopping.')
        return
    regatta_id = results[0]['objectId']
    pointer = {'__type': 'Pointer', 'className': 'regattas', 'objectId': regatta_id}
    print(f"Probing classes against regatta {regatta_id} ({results[0].get('name')!r})\n")
    for cls in CANDIDATE_CLASSES:
        for key in ('regattaObject', 'regatta'):
            code, payload = query(cls, {key: pointer})
            rows = payload.get('results') if isinstance(payload, dict) else None
            if rows:
                print(f'{cls} ({key}): HTTP {code}, {len(rows)} rows -- keys: {", ".join(sorted(rows[0]))}')
                print('   sample:', json.dumps(rows[0])[:400])
            else:
                err = payload.get('error') if isinstance(payload, dict) else payload
                print(f'{cls} ({key}): HTTP {code}, no rows' + (f' (error: {err})' if err else ''))


def main(argv):
    command = argv[1] if len(argv) > 1 else 'all'
    save_path = argv[argv.index('--save') + 1] if '--save' in argv else None
    if command in ('all', 'counts'):
        probe_counts()
        print()
    if command in ('all', 'sitemap'):
        probe_sitemap(save_path)
    if command == 'results':
        probe_results_classes()


if __name__ == '__main__':
    main(sys.argv)
