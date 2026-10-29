"""
Utility functions for RegattaResume
"""
from models import db, Result, Regatta
from datetime import datetime, timedelta
from sqlalchemy import func, desc
import tempfile
import os

import stats


def calculate_stats(sailor):
    """
    Comprehensive statistics for a sailor: ClubSpot results plus HS/College
    results filed under the same normalized name, with places normalized to
    a 100-boat fleet (see stats.py). Keeps the keys templates always used.
    """
    return stats.summarize(stats.entries_for_sailor(sailor))


def get_performance_trends(sailor_id, months_back=6, boat_type=None):
    """
    Recent-vs-historical trend for coach view on standardized finish
    (place per 100 boats, lower is better), optionally for one boat class.
    """
    from models import Sailor
    sailor = db.session.get(Sailor, sailor_id)
    entries = stats.entries_for_sailor(sailor) if sailor else []
    return stats.trends(entries, months_back=months_back, boat_type=boat_type)


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
