"""
Normalized sailing statistics across all three result sources.

A raw place says little without the fleet: 3rd of 4 and 3rd of 40 are
different achievements. Every result that has a fleet size is expressed as
a STANDARDIZED FINISH -- the place it would be in a 100-boat fleet
(place / fleet * 100, lower is better) -- and a PERCENTILE (share of the
fleet beaten). Results without a fleet size, from fleets smaller than
MIN_FLEET, without a finishing place (DNF, DNS...), or from regattas still
in progress are reported separately and kept out of the normalized numbers.
"""
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import desc

from models import db, Regatta, Result, SailorName, HSResult, CollegeResult


def min_fleet():
    """Smallest fleet whose places count toward normalized stats"""
    return int(os.environ.get('STATS_MIN_FLEET', '5'))


# ---------------------------------------------------------------------------
# The metric
# ---------------------------------------------------------------------------

def standardized_finish(place, fleet):
    """Place scaled to a 100-boat fleet; None without a usable fleet"""
    if not place or not fleet or fleet < 1:
        return None
    return round(place / fleet * 100, 1)


def percentile(place, fleet):
    """Share of the fleet finished behind this boat, 0-100; None for a fleet of one"""
    if not place or not fleet or fleet < 2:
        return None
    return round((fleet - place) / (fleet - 1) * 100, 1)


# ---------------------------------------------------------------------------
# Collecting results into one shape
# ---------------------------------------------------------------------------

def _entry(source, date, regatta, place, fleet, **extra):
    entry = {
        'source': source,          # 'clubspot' | 'hs' | 'college'
        'date': date,
        'regatta': regatta,
        'place': place,
        'fleet': fleet,
        'scoring_code': extra.get('scoring_code'),
        'division': extra.get('division'),
        'boat_type': extra.get('boat_type'),
        'role': extra.get('role'),
        'in_progress': bool(extra.get('in_progress')),
        'points': extra.get('points'),
        'location': extra.get('location'),
        'position_text': extra.get('position_text'),
    }
    entry['standardized'] = standardized_finish(place, fleet)
    entry['percentile'] = percentile(place, fleet)
    return entry


def clubspot_entries(sailor_id):
    rows = db.session.query(Result, Regatta).join(
        Regatta, Result.regatta_id == Regatta.id
    ).filter(Result.sailor_id == sailor_id).order_by(desc(Regatta.start_date)).all()
    return [_entry('clubspot', regatta.start_date, regatta.name, result.placement,
                   result.fleet_size, scoring_code=result.scoring_code,
                   division=result.division, boat_type=result.boat_type or regatta.fleet_type,
                   role=result.role, in_progress=(regatta.status == 'in_progress'),
                   points=result.points_scored, location=regatta.location)
            for result, regatta in rows]


def _school_entries(model, source, sailor_name_id):
    rows = model.query.filter_by(sailor_name_id=sailor_name_id).order_by(desc(model.regatta_date)).all()
    return [_entry(source, row.regatta_date, row.regatta_name, row.place_numeric, row.total_boats,
                   division=row.division, role=(row.position or '').lower() or None,
                   position_text=row.position, location=row.school)
            for row in rows]


def school_entries(sailor_name_id):
    return (_school_entries(HSResult, 'hs', sailor_name_id)
            + _school_entries(CollegeResult, 'college', sailor_name_id))


def entries_for_sailor(sailor):
    """ClubSpot results plus HS/College results for the same normalized name"""
    entries = clubspot_entries(sailor.id)
    match = SailorName.query.filter_by(name_normalized=sailor.name_normalized).first()
    if match:
        entries += school_entries(match.id)
    return entries


def entries_for_sailor_name(sailor_name):
    return school_entries(sailor_name.id)


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def _mean(values):
    return round(sum(values) / len(values), 1) if values else None


def _std(values):
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return round((sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5, 1)


def eligible(entries, floor=None):
    """Entries that count toward normalized stats, and why the rest don't"""
    floor = floor or min_fleet()
    counted, excluded = [], Counter()
    for e in entries:
        if e['in_progress']:
            excluded['in_progress'] += 1
        elif e['scoring_code'] or not e['place']:
            excluded['no_finish'] += 1
        elif not e['fleet']:
            excluded['no_fleet_size'] += 1
        elif e['fleet'] < floor:
            excluded['small_fleet'] += 1
        else:
            counted.append(e)
    return counted, excluded


def summarize(entries, months_recent=6, today=None):
    """
    One dict of headline numbers from a list of entries. Keys the templates
    already used are kept (total_regattas, best_finish, average_placement,
    top_3_count, top_10_count, most_sailed_fleet, skipper_count, crew_count,
    recent_regattas, years_active) and the normalized ones are added.
    """
    today = today or datetime.utcnow().date()
    counted, excluded = eligible(entries)
    finals = [e for e in entries if not e['in_progress'] and e['place']]
    standardized = [e['standardized'] for e in counted]
    percentiles = [e['percentile'] for e in counted if e['percentile'] is not None]

    best = min(counted, key=lambda e: e['standardized'], default=None)
    best_raw = min(finals, key=lambda e: e['place'], default=None)
    weighted = None
    if counted:
        weight = sum(e['fleet'] for e in counted)
        weighted = round(sum(e['standardized'] * e['fleet'] for e in counted) / weight, 1)

    dated = [e['date'] for e in entries if e['date']]
    recent_cutoff = today - timedelta(days=30 * months_recent)
    by_source = Counter(e['source'] for e in entries)
    boat_types = Counter(e['boat_type'] for e in entries if e['boat_type'])
    roles = Counter((e['role'] or '').lower() for e in entries)

    return {
        'total_regattas': len(entries),
        'counted_results': len(counted),
        'excluded': dict(excluded),
        'in_progress_count': excluded.get('in_progress', 0),
        'min_fleet': min_fleet(),
        # raw
        'best_finish': best_raw['place'] if best_raw else None,
        'best_finish_text': (f"{best_raw['place']} of {best_raw['fleet']}" if best_raw and best_raw['fleet']
                             else (str(best_raw['place']) if best_raw else None)),
        'average_placement': _mean([e['place'] for e in finals]),
        'top_3_count': sum(1 for e in finals if e['place'] <= 3),
        'top_10_count': sum(1 for e in finals if e['place'] <= 10),
        # normalized
        'best_standardized': best['standardized'] if best else None,
        'best_standardized_text': (f"{best['place']} of {best['fleet']} ({best['regatta']})" if best else None),
        'avg_standardized': _mean(standardized),
        'weighted_standardized': weighted,
        'avg_percentile': _mean(percentiles),
        'consistency': _std(standardized),
        # breakdowns
        'by_source': {s: by_source.get(s, 0) for s in ('clubspot', 'hs', 'college')},
        'hs_count': by_source.get('hs', 0),
        'college_count': by_source.get('college', 0),
        'clubspot_count': by_source.get('clubspot', 0),
        'most_sailed_fleet': boat_types.most_common(1)[0][0] if boat_types else None,
        'skipper_count': roles.get('skipper', 0),
        'crew_count': roles.get('crew', 0),
        'recent_regattas': sum(1 for e in entries if e['date'] and e['date'] >= recent_cutoff),
        'years_active': (max(dated).year - min(dated).year + 1) if dated else None,
        'first_regatta_date': min(dated) if dated else None,
        'last_regatta_date': max(dated) if dated else None,
    }


def breakdown(entries, key):
    """Per-group table: count, counted, avg standardized, best standardized, avg raw place"""
    groups = defaultdict(list)
    for e in entries:
        groups[e.get(key) or 'Unknown'].append(e)
    table = []
    for name, group in groups.items():
        counted, _ = eligible(group)
        table.append({
            'name': name,
            'count': len(group),
            'counted': len(counted),
            'avg_standardized': _mean([e['standardized'] for e in counted]),
            'best_standardized': min((e['standardized'] for e in counted), default=None),
            'avg_place': _mean([e['place'] for e in group if e['place'] and not e['in_progress']]),
        })
    table.sort(key=lambda row: (-row['count'], row['name']))
    return table


def trends(entries, months_back=6, boat_type=None, today=None):
    """
    Recent-vs-historical comparison on standardized finish, plus the chart
    series (one point per counted result). Mirrors the shape
    get_performance_trends used to return, with standardized values.
    """
    today = today or datetime.utcnow().date()
    cutoff = today - timedelta(days=months_back * 30)
    pool = [e for e in entries if not boat_type or e.get('boat_type') == boat_type]
    counted, excluded = eligible(pool)
    recent = [e for e in counted if e['date'] and e['date'] >= cutoff]
    historical = [e for e in counted if e['date'] and e['date'] < cutoff]

    recent_avg = _mean([e['standardized'] for e in recent])
    historical_avg = _mean([e['standardized'] for e in historical])
    improvement = None
    if recent_avg is not None and historical_avg is not None:
        improvement = round(historical_avg - recent_avg, 1)  # lower is better, so positive = improved

    chart = sorted((e for e in counted if e['date']), key=lambda e: e['date'])
    return {
        'recent_avg': recent_avg,
        'historical_avg': historical_avg,
        'improvement': improvement,
        'recent_top_3': sum(1 for e in recent if e['place'] <= 3),
        'recent_count': len(recent),
        'historical_count': len(historical),
        'recent_percentile': _mean([e['percentile'] for e in recent if e['percentile'] is not None]),
        'consistency': _std([e['standardized'] for e in recent]),
        'excluded': dict(excluded),
        'months_analyzed': months_back,
        'chart_data': [{
            'date': e['date'].isoformat(),
            'standardized': e['standardized'],
            'percentile': e['percentile'],
            'placement': e['place'],
            'fleet': e['fleet'],
            'regatta': e['regatta'],
            'source': e['source'],
        } for e in chart],
    }
