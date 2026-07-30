"""
Utility functions for RegattaResume
"""
from models import db, Result, Regatta
from datetime import datetime, timedelta
from sqlalchemy import func, desc
import tempfile
import os


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


def get_performance_trends(sailor_id, months_back=6):
    """
    Calculate performance trends for coach view

    Args:
        sailor_id: ID of the sailor
        months_back: How many months to consider as "recent"

    Returns:
        dict with trend analysis
    """
    cutoff_date = datetime.utcnow().date() - timedelta(days=months_back * 30)

    # Recent average placement
    recent_avg = db.session.query(
        func.avg(Result.placement)
    ).join(Regatta).filter(
        Result.sailor_id == sailor_id,
        Regatta.start_date >= cutoff_date
    ).scalar()

    # Historical average placement (before cutoff)
    historical_avg = db.session.query(
        func.avg(Result.placement)
    ).join(Regatta).filter(
        Result.sailor_id == sailor_id,
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
        Result.sailor_id == sailor_id,
        Regatta.start_date >= cutoff_date,
        Result.placement <= 3
    ).scalar() or 0

    # Get recent results for trend chart
    recent_results = db.session.query(
        Regatta.start_date,
        Result.placement,
        Regatta.name
    ).join(Result).filter(
        Result.sailor_id == sailor_id,
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

    sailor = resume_link.sailor

    # Get selected results
    if resume_link.selected_result_ids:
        results = db.session.query(Result, Regatta).join(
            Regatta, Result.regatta_id == Regatta.id
        ).filter(
            Result.id.in_(resume_link.selected_result_ids)
        ).order_by(desc(Regatta.start_date)).all()
    else:
        results = db.session.query(Result, Regatta).join(
            Regatta, Result.regatta_id == Regatta.id
        ).filter(
            Result.sailor_id == sailor.id
        ).order_by(desc(Regatta.start_date)).all()

    # Render HTML template
    html_content = render_template(
        f'resume_{resume_link.template_style}_pdf.html',
        sailor=sailor,
        resume_link=resume_link,
        results=results,
        stats=calculate_stats(sailor)
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
