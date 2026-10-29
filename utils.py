"""
Utility functions for RegattaResume
"""
from models import (db, Result, Regatta, HSResult, CollegeResult, ManualResult,
                    SailorName)
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
import tempfile
import os

from sqlalchemy import func, desc


# ============================================================================
# RESUME ENTRIES
#
# A resume can mix four sources whose rows have incompatible shapes and whose
# primary keys collide (hs_results.id 5 and college_results.id 5 are different
# regattas). Everything downstream -- the builder, the shared page, the PDF --
# goes through the qualified key and the normalized ResumeEntry below, so there
# is exactly one place that knows the per-source quirks.
# ============================================================================

KIND_CLUBSPOT = 'clubspot'
KIND_HS = 'hs'
KIND_COLLEGE = 'college'
KIND_MANUAL = 'manual'

SOURCE_LABELS = {
    KIND_CLUBSPOT: 'ClubSpot',
    KIND_HS: 'High School',
    KIND_COLLEGE: 'College',
    KIND_MANUAL: 'Self-reported',
}


@dataclass
class ResumeEntry:
    """One regatta result, normalized across all four sources."""
    key: str
    kind: str
    regatta_name: str
    date: Optional[object] = None          # datetime.date
    place_numeric: Optional[int] = None
    total_boats: Optional[int] = None
    boat_or_division: Optional[str] = None
    role: Optional[str] = None
    team: Optional[str] = None
    location: Optional[str] = None
    link: Optional[str] = None
    notes: Optional[str] = None
    is_self_reported: bool = False
    is_verified: bool = True

    @property
    def source_label(self):
        return SOURCE_LABELS.get(self.kind, self.kind)

    @property
    def place_display(self):
        """
        "3/42" when the fleet size is known, else "3".

        ClubSpot rows carry no fleet size at all, while HS/college rows carry
        both halves -- hence the two shapes.
        """
        if self.place_numeric is None:
            return '--'
        if self.total_boats:
            return f'{self.place_numeric}/{self.total_boats}'
        return str(self.place_numeric)

    @property
    def year(self):
        return self.date.year if self.date else None

    @property
    def is_podium(self):
        return self.place_numeric is not None and self.place_numeric <= 3


def make_result_key(kind, obj_id):
    """Qualified key for a result row, e.g. "hs:8841"."""
    return f'{kind}:{obj_id}'


def parse_result_key(raw):
    """
    Parse a qualified key into (kind, id).

    A bare int is a legacy ClubSpot `Result.id` from before the keys were
    qualified; migration 010 rewrites stored rows, but accept them anyway so an
    un-migrated row or an old client never silently drops results.
    """
    if raw is None:
        return None
    if isinstance(raw, int):
        return (KIND_CLUBSPOT, raw)

    text = str(raw)
    if ':' not in text:
        return (KIND_CLUBSPOT, int(text)) if text.isdigit() else None

    kind, _, id_part = text.partition(':')
    if kind not in SOURCE_LABELS or not id_part.isdigit():
        return None
    return (kind, int(id_part))


def _entry_from_clubspot(result, regatta):
    return ResumeEntry(
        key=make_result_key(KIND_CLUBSPOT, result.id),
        kind=KIND_CLUBSPOT,
        regatta_name=regatta.name,
        date=regatta.start_date,
        place_numeric=result.placement,
        total_boats=None,  # ClubSpot results carry no fleet size
        boat_or_division=result.boat_type or result.division,
        role=result.role,
        team=result.team_name,
        location=regatta.location,
        link=regatta.source_url,
    )


def _entry_from_scored(row, kind):
    """HSResult / CollegeResult share a shape."""
    return ResumeEntry(
        key=make_result_key(kind, row.id),
        kind=kind,
        regatta_name=row.regatta_name,
        date=row.regatta_date,
        place_numeric=row.place_numeric,
        total_boats=row.total_boats,
        boat_or_division=row.division,
        role=row.position,
        team=row.school,
        link=row.regatta_link,
    )


def _entry_from_manual(row):
    return ResumeEntry(
        key=make_result_key(KIND_MANUAL, row.id),
        kind=KIND_MANUAL,
        regatta_name=row.regatta_name,
        date=row.regatta_date,
        place_numeric=row.place_numeric,
        total_boats=row.total_boats,
        boat_or_division=row.boat_type or row.division,
        role=row.role,
        team=row.team_name,
        location=row.location,
        link=row.source_url,
        notes=row.notes,
        is_self_reported=True,
        is_verified=bool(row.verified),
    )


def _sort_entries(entries):
    """Newest first; undated entries last rather than crashing the sort."""
    return sorted(
        entries,
        key=lambda e: (e.date is not None, e.date or datetime.min.date()),
        reverse=True
    )


def collect_user_entries(user):
    """
    Every result available to this user, grouped by kind, for the builder.

    Pulls across all claimed profiles -- a sailor usually holds a ClubSpot
    profile and one or more HS/college names, and the point of the resume is to
    put them on one page.
    """
    grouped = {k: [] for k in (KIND_CLUBSPOT, KIND_HS, KIND_COLLEGE, KIND_MANUAL)}
    if user is None or not user.is_authenticated:
        return grouped

    sailor_ids = [s.id for s in user.claimed_sailors]
    name_ids = [n.id for n in user.claimed_names]

    if sailor_ids:
        rows = db.session.query(Result, Regatta).join(
            Regatta, Result.regatta_id == Regatta.id
        ).filter(Result.sailor_id.in_(sailor_ids)).all()
        grouped[KIND_CLUBSPOT] = [_entry_from_clubspot(r, g) for r, g in rows]

    if name_ids:
        grouped[KIND_HS] = [
            _entry_from_scored(r, KIND_HS) for r in
            HSResult.query.filter(HSResult.sailor_name_id.in_(name_ids)).all()
        ]
        grouped[KIND_COLLEGE] = [
            _entry_from_scored(r, KIND_COLLEGE) for r in
            CollegeResult.query.filter(CollegeResult.sailor_name_id.in_(name_ids)).all()
        ]

    grouped[KIND_MANUAL] = [
        _entry_from_manual(r) for r in
        ManualResult.query.filter_by(user_id=user.id).all()
    ]

    return {k: _sort_entries(v) for k, v in grouped.items()}


def resolve_resume_entries(resume_link):
    """
    The entries a resume should display, newest first.

    One grouped query per kind rather than one per selected row. Used by both
    the shared page and the PDF export, which previously duplicated this
    selection logic and could drift apart.
    """
    selected = resume_link.selected_result_ids

    if not selected:
        return _default_entries(resume_link)

    by_kind = {}
    for raw in selected:
        parsed = parse_result_key(raw)
        if parsed:
            by_kind.setdefault(parsed[0], []).append(parsed[1])

    entries = []

    if by_kind.get(KIND_CLUBSPOT):
        rows = db.session.query(Result, Regatta).join(
            Regatta, Result.regatta_id == Regatta.id
        ).filter(Result.id.in_(by_kind[KIND_CLUBSPOT])).all()
        entries += [_entry_from_clubspot(r, g) for r, g in rows]

    if by_kind.get(KIND_HS):
        entries += [_entry_from_scored(r, KIND_HS) for r in
                    HSResult.query.filter(HSResult.id.in_(by_kind[KIND_HS])).all()]

    if by_kind.get(KIND_COLLEGE):
        entries += [_entry_from_scored(r, KIND_COLLEGE) for r in
                    CollegeResult.query.filter(
                        CollegeResult.id.in_(by_kind[KIND_COLLEGE])).all()]

    if by_kind.get(KIND_MANUAL):
        # Scope manual results to the resume's owner: these ids come from a
        # stored selection, and a result the owner no longer owns must not
        # keep rendering on their page.
        query = ManualResult.query.filter(ManualResult.id.in_(by_kind[KIND_MANUAL]))
        if resume_link.user_id:
            query = query.filter(ManualResult.user_id == resume_link.user_id)
        entries += [_entry_from_manual(r) for r in query.all()]

    return _sort_entries(entries)


def _default_entries(resume_link):
    """Everything the owner has, for a resume saved with no explicit selection."""
    if resume_link.user_id and resume_link.user:
        grouped = collect_user_entries(resume_link.user)
        return _sort_entries([e for group in grouped.values() for e in group])

    # Legacy link with no user_id: fall back to the ClubSpot sailor it was
    # created for.
    if resume_link.sailor_id:
        rows = db.session.query(Result, Regatta).join(
            Regatta, Result.regatta_id == Regatta.id
        ).filter(Result.sailor_id == resume_link.sailor_id).all()
        return _sort_entries([_entry_from_clubspot(r, g) for r, g in rows])

    return []


def resume_display_name(resume_link):
    """
    Name to print at the top of a resume.

    The account's chosen name wins over the scraped spelling: the scraped rows
    carry whatever the regatta organizers typed.
    """
    if resume_link.user:
        return resume_link.user.resume_name
    if resume_link.sailor:
        return resume_link.sailor.name
    return 'Sailing Resume'


def resume_subtitle(resume_link):
    """Club/school line under the name, if we have one."""
    user = resume_link.user
    if user and user.primary_school:
        return user.primary_school
    if resume_link.sailor and resume_link.sailor.home_club:
        return resume_link.sailor.home_club
    if user:
        for name in user.claimed_names:
            if name.school:
                return name.school
    return None


def stats_from_entries(entries):
    """
    Resume stats computed from the displayed entries.

    `calculate_stats` only knows ClubSpot `Result` rows, so it reports zeros for
    an HS/college sailor. This works off whatever is actually on the resume.
    """
    placements = [e.place_numeric for e in entries if e.place_numeric is not None]
    years = [e.year for e in entries if e.year]

    return {
        'total_regattas': len(entries),
        'best_finish': min(placements) if placements else None,
        'average_placement': round(sum(placements) / len(placements), 1) if placements else None,
        'top_3_count': len([p for p in placements if p <= 3]),
        'top_10_count': len([p for p in placements if p <= 10]),
        'years_active': (max(years) - min(years) + 1) if years else None,
        'first_year': min(years) if years else None,
        'last_year': max(years) if years else None,
        'self_reported_count': len([e for e in entries if e.is_self_reported]),
        'source_counts': {
            SOURCE_LABELS[k]: len([e for e in entries if e.kind == k])
            for k in (KIND_CLUBSPOT, KIND_HS, KIND_COLLEGE, KIND_MANUAL)
            if any(e.kind == k for e in entries)
        },
    }


def calculate_stats(sailor):
    """
    Calculate comprehensive statistics for a sailor

    Returns:
        dict with various statistics
    """
    # Total regattas
    total_regattas = db.session.query(
        func.count(func.distinct(Result.regatta_id))
    ).filter(Result.sailor_id == sailor.id).scalar() or 0

    # Best finish
    best_finish = db.session.query(
        func.min(Result.placement)
    ).filter(Result.sailor_id == sailor.id).scalar()

    # Average placement
    avg_placement = db.session.query(
        func.avg(Result.placement)
    ).filter(Result.sailor_id == sailor.id).scalar()

    # Top 3 finishes
    top_3_count = Result.query.filter(
        Result.sailor_id == sailor.id,
        Result.placement <= 3
    ).count()

    # Top 10 finishes
    top_10_count = Result.query.filter(
        Result.sailor_id == sailor.id,
        Result.placement <= 10
    ).count()

    # Most sailed fleet
    fleet_data = db.session.query(
        Result.boat_type,
        func.count(Result.id).label('count')
    ).filter(
        Result.sailor_id == sailor.id,
        Result.boat_type.isnot(None)
    ).group_by(Result.boat_type).order_by(desc('count')).first()

    most_sailed_fleet = fleet_data[0] if fleet_data else None

    # Role breakdown (skipper vs crew)
    skipper_count = Result.query.filter(
        Result.sailor_id == sailor.id,
        Result.role == 'skipper'
    ).count()

    crew_count = Result.query.filter(
        Result.sailor_id == sailor.id,
        Result.role == 'crew'
    ).count()

    # Recent activity (last 6 months)
    six_months_ago = datetime.utcnow().date() - timedelta(days=180)
    recent_regattas = db.session.query(
        func.count(func.distinct(Result.regatta_id))
    ).join(Regatta).filter(
        Result.sailor_id == sailor.id,
        Regatta.start_date >= six_months_ago
    ).scalar() or 0

    # Years active (first to last regatta)
    date_range = db.session.query(
        func.min(Regatta.start_date),
        func.max(Regatta.start_date)
    ).join(Result).filter(
        Result.sailor_id == sailor.id
    ).first()

    years_active = None
    if date_range and date_range[0] and date_range[1]:
        years_active = (date_range[1].year - date_range[0].year) + 1

    return {
        'total_regattas': total_regattas,
        'best_finish': best_finish,
        'average_placement': round(avg_placement, 1) if avg_placement else None,
        'top_3_count': top_3_count,
        'top_10_count': top_10_count,
        'most_sailed_fleet': most_sailed_fleet,
        'skipper_count': skipper_count,
        'crew_count': crew_count,
        'recent_regattas': recent_regattas,
        'years_active': years_active,
        'first_regatta_date': date_range[0] if date_range else None,
        'last_regatta_date': date_range[1] if date_range else None
    }


def get_performance_trends(sailor_id, months_back=6, boat_type=None):
    """
    Calculate performance trends for coach view

    Args:
        sailor_id: ID of the sailor
        months_back: How many months to consider as "recent"
        boat_type: Optional boat class to restrict the analysis to

    Returns:
        dict with trend analysis
    """
    cutoff_date = datetime.utcnow().date() - timedelta(days=months_back * 30)

    conditions = [Result.sailor_id == sailor_id]
    if boat_type:
        conditions.append(Result.boat_type == boat_type)

    # Recent average placement
    recent_avg = db.session.query(
        func.avg(Result.placement)
    ).join(Regatta).filter(
        *conditions,
        Regatta.start_date >= cutoff_date
    ).scalar()

    # Historical average placement (before cutoff)
    historical_avg = db.session.query(
        func.avg(Result.placement)
    ).join(Regatta).filter(
        *conditions,
        Regatta.start_date < cutoff_date
    ).scalar()

    # Improvement calculation
    improvement = None
    if recent_avg and historical_avg:
        # Lower is better in sailing, so negative difference means improvement
        improvement = historical_avg - recent_avg

    # Recent top finishes
    recent_top_3 = db.session.query(
        func.count(Result.id)
    ).join(Regatta).filter(
        *conditions,
        Regatta.start_date >= cutoff_date,
        Result.placement <= 3
    ).scalar() or 0

    # Get recent results for trend chart
    recent_results = db.session.query(
        Regatta.start_date,
        Result.placement,
        Regatta.name
    ).join(Result).filter(
        *conditions,
        Regatta.start_date >= cutoff_date
    ).order_by(Regatta.start_date).all()

    chart_data = [{
        'date': r.start_date.isoformat(),
        'placement': r.placement,
        'regatta': r.name
    } for r in recent_results]

    # Consistency score (std deviation of placements)
    placements = [r.placement for r in recent_results]
    consistency = calculate_consistency(placements) if placements else None

    return {
        'recent_avg': round(recent_avg, 1) if recent_avg else None,
        'historical_avg': round(historical_avg, 1) if historical_avg else None,
        'improvement': round(improvement, 1) if improvement else None,
        'recent_top_3': recent_top_3,
        'chart_data': chart_data,
        'consistency': consistency,
        'months_analyzed': months_back
    }


def calculate_consistency(placements):
    """
    Calculate consistency score from list of placements
    Lower score = more consistent

    Returns:
        float: Standard deviation of placements
    """
    if not placements or len(placements) < 2:
        return None

    mean = sum(placements) / len(placements)
    variance = sum((x - mean) ** 2 for x in placements) / len(placements)
    std_dev = variance ** 0.5

    return round(std_dev, 2)


def generate_pdf(resume_link):
    """
    Generate PDF from resume link

    Args:
        resume_link: ResumeLink object

    Returns:
        str: Path to generated PDF file
    """
    from flask import render_template, current_app
    from weasyprint import HTML
    import tempfile

    entries = resolve_resume_entries(resume_link)

    # Render HTML template
    html_content = render_template(
        f'resume_{resume_link.template_style}_pdf.html',
        sailor=resume_link.sailor,
        owner=resume_link.user,
        display_name=resume_display_name(resume_link),
        subtitle=resume_subtitle(resume_link),
        resume_link=resume_link,
        entries=entries,
        stats=stats_from_entries(entries)
    )

    # Generate PDF
    pdf_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')

    HTML(string=html_content, base_url=current_app.config['BASE_URL']).write_pdf(
        pdf_file.name
    )

    return pdf_file.name


def format_placement(placement):
    """
    Format placement number with ordinal suffix

    Args:
        placement: int (e.g., 1, 2, 3, 21)

    Returns:
        str: Formatted placement (e.g., "1st", "2nd", "3rd", "21st")
    """
    if placement is None:
        return "N/A"

    suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(
        placement if placement < 20 else placement % 10,
        'th'
    )

    return f"{placement}{suffix}"


def get_recent_achievements(sailor_id, limit=5):
    """
    Get recent notable achievements for a sailor

    Returns:
        list of achievement dicts
    """
    achievements = []

    # Recent podium finishes
    recent_podiums = db.session.query(Result, Regatta).join(
        Regatta, Result.regatta_id == Regatta.id
    ).filter(
        Result.sailor_id == sailor_id,
        Result.placement <= 3
    ).order_by(desc(Regatta.start_date)).limit(limit).all()

    for result, regatta in recent_podiums:
        achievements.append({
            'type': 'podium',
            'text': f"{format_placement(result.placement)} at {regatta.name}",
            'date': regatta.start_date,
            'placement': result.placement
        })

    return achievements
