"""
Probe theclubspot.com's Parse API for a Chrome-free way to read results.

The scraper already gets the regatta LIST from this API; if one of the
classes below also serves per-sailor RESULTS, the whole Selenium/Chrome
dependency can be deleted. Run this on your own machine (it needs the
`requests` package and open internet access):

    python scripts/probe_clubspot_api.py

Then paste the full output back into the Claude session.
"""
import json

import requests

PARSE_URL = 'https://theclubspot.com/parse/classes/{}'
CREDS = {
    '_method': 'GET',
    '_ApplicationId': 'myclubspot2017',
    '_ClientVersion': 'js4.3.1-forked-1.0',
    '_InstallationId': 'ce500aaa-c2a0-4d06-a9e3-1a558a606542',
}
HEADERS = {
    'Content-Type': 'text/plain',
    'Origin': 'https://theclubspot.com',
    'Referer': 'https://theclubspot.com/events',
    'User-Agent': 'Mozilla/5.0',
}

CANDIDATE_CLASSES = [
    'scores', 'results', 'raceResults', 'race_results', 'finishes',
    'entries', 'registrations', 'boats', 'teams', 'classes', 'divisions',
    'races', 'participants', 'sailors', 'crews', 'standings', 'placements',
]


def query(cls, where, limit=3, extra=None):
    data = dict(CREDS, where=where, limit=limit)
    if extra:
        data.update(extra)
    resp = requests.post(PARSE_URL.format(cls), headers=HEADERS, json=data, timeout=30)
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {'raw': resp.text[:200]}


def main():
    # A recent public regatta to probe against
    code, payload = query(
        'regattas',
        {'public': True, 'archived': {'$ne': True}},
        limit=1,
        extra={'order': '-startDate', 'keys': 'objectId,name,startDate'},
    )
    print('regattas:', code, json.dumps(payload)[:300])
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
                first_keys = ', '.join(sorted(rows[0].keys()))
                print(f'{cls} ({key}): HTTP {code}, {len(rows)} rows -- keys: {first_keys}')
                print('   sample:', json.dumps(rows[0])[:400])
            else:
                err = payload.get('error') if isinstance(payload, dict) else payload
                print(f'{cls} ({key}): HTTP {code}, no rows'
                      + (f' (error: {err})' if err else ''))


if __name__ == '__main__':
    main()
