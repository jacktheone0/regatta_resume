from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_file, Response, stream_with_context, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_migrate import Migrate
from apscheduler.schedulers.background import BackgroundScheduler
from config import config
from models import (db, User, Sailor, Regatta, Result, ResumeLink, SailorName,
                   HSResult, CollegeResult, ScraperLogEntry, ProfileClaim,
                   ManualResult, CLAIM_CLUBSPOT, CLAIM_SAILOR_NAME)
from forms import (LoginForm, RegisterForm, ClaimProfileForm, AccountForm,
                   ManualResultForm)
from utils import (generate_pdf, calculate_stats, get_performance_trends,
                   resolve_resume_entries, collect_user_entries,
                   stats_from_entries, resume_display_name, resume_subtitle,
                   parse_result_key, KIND_CLUBSPOT, KIND_HS, KIND_COLLEGE,
                   KIND_MANUAL)
from mailer import send_verification_email, send_claim_notification
import click
import os
import json
import time
import requests
from functools import wraps
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import desc, func, text, or_

# Initialize Flask app
app = Flask(__name__)
env = os.environ.get('FLASK_ENV', 'development')
app.config.from_object(config[env])

# Log database configuration (without exposing credentials)
db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', 'Not set')
db_type = 'PostgreSQL' if 'postgresql://' in db_uri else 'SQLite' if 'sqlite:///' in db_uri else 'Unknown'
app.logger.info(f"Starting RegattaResume in {env} mode with {db_type} database")

# Initialize extensions
db.init_app(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ============================================================================
# AUTHORIZATION
# ============================================================================

def admin_required(view):
    """
    Restrict a view to admins.

    Until this existed, /admin and every /api/scraper/* endpoint were only
    @login_required -- so any registered user could browse the database and
    start or stop scraper runs.
    """
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def require_claim_or_redirect():
    """
    Common guard for the owner-only pages.

    Returns a redirect response when the user has not claimed anything yet,
    otherwise None.
    """
    if not current_user.has_claim:
        flash('Claim your sailor profile first to build a resume.', 'info')
        return redirect(url_for('claim_profile'))
    return None


# ============================================================================
# SCHEDULER SETUP
#
# The weekly ClubSpot scrape now runs on GitHub Actions
# (.github/workflows/scrape.yml), because headless Chrome does not fit in
# this container's memory allowance. Keep SCRAPER_ENABLED=false in any
# deployment that cannot spare ~1GB of RAM; setting it true restores the
# in-process schedule for local use.
# ============================================================================

def scheduled_scraper_job():
    """Scraper job that runs on schedule"""
    with app.app_context():
        try:
            app.logger.info("Starting scheduled scraper job...")
            from scraper import run_scraper  # imports selenium; keep it lazy
            run_scraper()
            app.logger.info("Scheduled scraper job completed successfully")
        except Exception as e:
            app.logger.error(f"Scheduled scraper job failed: {e}")

if app.config['SCRAPER_ENABLED']:
    scheduler = BackgroundScheduler(timezone=app.config['SCHEDULER_TIMEZONE'])
    # Run every Sunday at 22:00 America/Los_Angeles (DST-aware)
    scheduler.add_job(
        scheduled_scraper_job,
        'cron',
        day_of_week='sun',
        hour=22,
        minute=0,
        timezone=ZoneInfo('America/Los_Angeles'),
        id='weekly_scraper'
    )
    scheduler.start()
    app.logger.info("Scheduler started: Scraper will run every Sunday at 10:00 PM Pacific")


# ============================================================================
# PUBLIC ROUTES
# ============================================================================

@app.route('/')
def index():
    """Landing page with search"""
    recent_sailors = Sailor.query.order_by(desc(Sailor.updated_at)).limit(10).all()
    total_sailors = db.session.query(
        func.count(
            func.distinct(
                HSResult.sailor_name_id
            )
        )
    ).scalar() or 0
    total_sailors += db.session.query(
        func.count(
            func.distinct(
                CollegeResult.sailor_name_id
            )
        )
    ).filter(
        ~CollegeResult.sailor_name_id.in_(
            db.session.query(HSResult.sailor_name_id)
        )
    ).scalar() or 0

    total_regattas = db.session.query(
        func.count(
            func.distinct(
                HSResult.regatta_name
            )
        )
    ).scalar() or 0
    total_regattas += db.session.query(
        func.count(
            func.distinct(
                CollegeResult.regatta_name
            )
        )
    ).filter(
        ~CollegeResult.regatta_name.in_(
            db.session.query(HSResult.regatta_name)
        )
    ).scalar() or 0

    return render_template('index.html',
                         recent_sailors=recent_sailors,
                         total_sailors=total_sailors,
                         total_regattas=total_regattas)


@app.route('/search')
def search():
    """Search for sailors - searches both Sailor and SailorName tables"""
    query = request.args.get('q', '').strip()

    if not query:
        return jsonify([])

    # Match each word independently so "John Smith" still finds
    # "John A. Smith" or "Smith, John" (a contiguous-substring match
    # made results vanish once the full name was typed).
    tokens = [t for t in query.lower().split() if t]

    results = []

    # Search in Sailor table (ClubSpot data)
    sailor_query = Sailor.query
    for token in tokens:
        sailor_query = sailor_query.filter(Sailor.name_normalized.contains(token))
    sailors = sailor_query.limit(10).all()

    for s in sailors:
        results.append({
            'id': s.id,
            'name': s.name,
            'home_club': s.home_club,
            'total_regattas': s.total_regattas,
            'best_finish': s.best_finish,
            'source': 'clubspot',
            'url': f'/sailor/{s.id}'
        })

    # Search in SailorName table (HS/College data)
    sn_query = SailorName.query
    for token in tokens:
        sn_query = sn_query.filter(SailorName.name_normalized.contains(token))
    sailor_names = sn_query.limit(10).all()

    # Aggregate counts/best finishes in two grouped queries instead of
    # four queries per matched sailor
    sn_ids = [sn.id for sn in sailor_names]
    hs_stats, college_stats = {}, {}
    if sn_ids:
        hs_stats = {row[0]: (row[1], row[2]) for row in db.session.query(
            HSResult.sailor_name_id, func.count(HSResult.id), func.min(HSResult.place_numeric)
        ).filter(HSResult.sailor_name_id.in_(sn_ids)).group_by(HSResult.sailor_name_id).all()}
        college_stats = {row[0]: (row[1], row[2]) for row in db.session.query(
            CollegeResult.sailor_name_id, func.count(CollegeResult.id), func.min(CollegeResult.place_numeric)
        ).filter(CollegeResult.sailor_name_id.in_(sn_ids)).group_by(CollegeResult.sailor_name_id).all()}

    for sn in sailor_names:
        hs_count, best_hs = hs_stats.get(sn.id, (0, None))
        college_count, best_college = college_stats.get(sn.id, (0, None))
        total_results = hs_count + college_count

        best_finish = min(
            (b for b in (best_hs, best_college) if b is not None),
            default=None
        )

        source_label = sn.source.upper() if sn.source != 'both' else 'HS/College'

        results.append({
            'id': sn.id,
            'name': sn.name,
            'home_club': sn.school,
            'total_regattas': total_results,
            'best_finish': best_finish,
            'source': source_label,
            'url': f'/sailor-name/{sn.id}'
        })

    return jsonify(results)


@app.route('/sailor/<int:sailor_id>')
def sailor_profile(sailor_id):
    """Sailor profile page - public view"""
    sailor = Sailor.query.get_or_404(sailor_id)

    # Get all results with regatta info
    results = db.session.query(Result, Regatta).join(
        Regatta, Result.regatta_id == Regatta.id
    ).filter(
        Result.sailor_id == sailor_id
    ).order_by(
        desc(Regatta.start_date)
    ).all()

    # Calculate statistics
    stats = calculate_stats(sailor)

    # Check if current user owns this profile
    is_owner = current_user.is_authenticated and current_user.owns(CLAIM_CLUBSPOT, sailor_id)

    # Also check for HS/College results by matching sailor name
    sailor_name_match = SailorName.query.filter_by(
        name_normalized=sailor.name_normalized
    ).first()

    hs_results = []
    college_results = []
    if sailor_name_match:
        hs_results = HSResult.query.filter_by(
            sailor_name_id=sailor_name_match.id
        ).order_by(desc(HSResult.regatta_date)).all()

        college_results = CollegeResult.query.filter_by(
            sailor_name_id=sailor_name_match.id
        ).order_by(desc(CollegeResult.regatta_date)).all()

    return render_template('sailor_profile.html',
                         sailor=sailor,
                         results=results,
                         stats=stats,
                         is_owner=is_owner,
                         hs_results=hs_results,
                         college_results=college_results)


@app.route('/sailor-name/<int:sailor_name_id>')
def sailor_name_profile(sailor_name_id):
    """Profile page for HS/College sailors from SailorName table"""
    sailor_name = SailorName.query.get_or_404(sailor_name_id)

    # Get HS results
    hs_results = HSResult.query.filter_by(
        sailor_name_id=sailor_name_id
    ).order_by(desc(HSResult.regatta_date)).all()

    # Get College results
    college_results = CollegeResult.query.filter_by(
        sailor_name_id=sailor_name_id
    ).order_by(desc(CollegeResult.regatta_date)).all()

    # Calculate basic stats
    total_results = len(hs_results) + len(college_results)

    best_finish = None
    if hs_results or college_results:
        all_placements = [r.place_numeric for r in hs_results if r.place_numeric] + \
                        [r.place_numeric for r in college_results if r.place_numeric]
        if all_placements:
            best_finish = min(all_placements)

    avg_placement = None
    if hs_results or college_results:
        all_placements = [r.place_numeric for r in hs_results if r.place_numeric] + \
                        [r.place_numeric for r in college_results if r.place_numeric]
        if all_placements:
            avg_placement = round(sum(all_placements) / len(all_placements), 1)

    top_3_count = len([r for r in hs_results if r.place_numeric and r.place_numeric <= 3]) + \
                  len([r for r in college_results if r.place_numeric and r.place_numeric <= 3])

    top_10_count = len([r for r in hs_results if r.place_numeric and r.place_numeric <= 10]) + \
                   len([r for r in college_results if r.place_numeric and r.place_numeric <= 10])

    stats = {
        'total_regattas': total_results,
        'best_finish': best_finish,
        'average_placement': avg_placement,
        'top_3_count': top_3_count,
        'top_10_count': top_10_count,
        'hs_count': len(hs_results),
        'college_count': len(college_results)
    }

    # Check if user owns this (if they have claimed it)
    is_owner = current_user.is_authenticated and current_user.owns(
        CLAIM_SAILOR_NAME, sailor_name_id)

    return render_template('sailor_name_profile.html',
                         sailor_name=sailor_name,
                         hs_results=hs_results,
                         college_results=college_results,
                         stats=stats,
                         is_owner=is_owner)


@app.route('/resume-builder')
@login_required
def resume_builder():
    """
    Resume builder for the logged-in user.

    Was /sailor/<id>/resume-builder, which only ever worked for a ClubSpot
    profile. The builder now draws on every claimed profile plus the sailor's
    own added results.
    """
    redirect_response = require_claim_or_redirect()
    if redirect_response:
        return redirect_response

    grouped = collect_user_entries(current_user)
    total = sum(len(v) for v in grouped.values())

    return render_template('resume_builder.html',
                         grouped=grouped,
                         total_entries=total,
                         section_order=[KIND_CLUBSPOT, KIND_HS, KIND_COLLEGE, KIND_MANUAL],
                         existing_links=ResumeLink.query.filter_by(
                             user_id=current_user.id
                         ).order_by(desc(ResumeLink.created_at)).all())


@app.route('/sailor/<int:sailor_id>/resume-builder')
@login_required
def resume_builder_legacy(sailor_id):
    """Old per-sailor builder URL; kept so existing bookmarks still land."""
    return redirect(url_for('resume_builder'))


@app.route('/coach-view/<int:sailor_id>')
def coach_view(sailor_id):
    """Coach analytics view for a sailor"""
    sailor = Sailor.query.get_or_404(sailor_id)

    # Get configurable time range from query params
    months_back = int(request.args.get('months', 6))  # Default: 6 months
    cutoff_date = datetime.utcnow().date() - timedelta(days=months_back * 30)

    # Boat-class filter: only accept classes this sailor has actually raced
    boat_types = [b[0] for b in db.session.query(Result.boat_type).filter(
        Result.sailor_id == sailor_id,
        Result.boat_type.isnot(None)
    ).distinct().order_by(Result.boat_type).all()]

    boat_filter = request.args.get('boat', '').strip() or None
    if boat_filter and boat_filter not in boat_types:
        boat_filter = None

    # Get recent vs historical results
    recent_query = db.session.query(Result, Regatta).join(
        Regatta, Result.regatta_id == Regatta.id
    ).filter(
        Result.sailor_id == sailor_id,
        Regatta.start_date >= cutoff_date
    )
    historical_query = db.session.query(Result, Regatta).join(
        Regatta, Result.regatta_id == Regatta.id
    ).filter(
        Result.sailor_id == sailor_id,
        Regatta.start_date < cutoff_date
    )
    if boat_filter:
        recent_query = recent_query.filter(Result.boat_type == boat_filter)
        historical_query = historical_query.filter(Result.boat_type == boat_filter)

    recent_results = recent_query.order_by(desc(Regatta.start_date)).all()
    historical_results = historical_query.order_by(desc(Regatta.start_date)).all()

    # Calculate performance trends
    trends = get_performance_trends(sailor_id, months_back, boat_type=boat_filter)

    # Get fleet breakdown (always across all classes, for comparison)
    fleet_stats = db.session.query(
        Result.boat_type,
        func.count(Result.id).label('count'),
        func.avg(Result.placement).label('avg_placement')
    ).filter(
        Result.sailor_id == sailor_id,
        Result.boat_type.isnot(None)
    ).group_by(Result.boat_type).all()

    return render_template('coach_view.html',
                         sailor=sailor,
                         recent_results=recent_results,
                         historical_results=historical_results,
                         trends=trends,
                         fleet_stats=fleet_stats,
                         months_back=months_back,
                         boat_types=boat_types,
                         boat_filter=boat_filter)


@app.route('/resume/<token>')
def shared_resume(token):
    """View a shared resume link"""
    resume_link = ResumeLink.query.filter_by(token=token).first_or_404()

    # Update access stats
    resume_link.last_accessed = datetime.utcnow()
    resume_link.access_count += 1
    db.session.commit()

    entries = resolve_resume_entries(resume_link)

    # Guard the template name: it comes from stored data, and an unexpected
    # value would otherwise be a path into render_template.
    style = resume_link.template_style if resume_link.template_style in (
        'modern', 'classic', 'minimal') else 'modern'

    return render_template(f'resume_{style}.html',
                         sailor=resume_link.sailor,
                         owner=resume_link.user,
                         display_name=resume_display_name(resume_link),
                         subtitle=resume_subtitle(resume_link),
                         resume_link=resume_link,
                         entries=entries,
                         stats=stats_from_entries(entries))


@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin dashboard with scraper controls"""
    # Calculate database stats
    stats = {
        'total_sailors': Sailor.query.count(),
        'total_regattas': Regatta.query.count(),
        'total_results': Result.query.count()
    }

    return render_template('admin.html', stats=stats)


@app.route('/admin/stats.json')
@admin_required
def admin_stats_json():
    """Database stats + latest scraper section completions for in-place refresh"""
    # Most recent completion row per step (rows with step set are written
    # only at section boundaries by scraper_schools._db_log)
    latest_ids = [row[0] for row in db.session.query(
        func.max(ScraperLogEntry.id)
    ).filter(ScraperLogEntry.step.isnot(None)).group_by(ScraperLogEntry.step).all()]

    step_order = ['schools', 'rosters', 'results']
    step_labels = {'schools': 'Step 1 · Schools', 'rosters': 'Step 2 · Rosters', 'results': 'Step 3 · Results'}

    progress_rows = []
    if latest_ids:
        progress_rows = ScraperLogEntry.query.filter(ScraperLogEntry.id.in_(latest_ids)).all()
        progress_rows.sort(key=lambda e: step_order.index(e.step) if e.step in step_order else 99)

    return jsonify({
        'total_sailors': Sailor.query.count(),
        'total_regattas': Regatta.query.count(),
        'total_results': Result.query.count(),
        'progress': [{
            'step': e.step,
            'step_label': step_labels.get(e.step, e.step),
            'section': e.section,
            'season': e.season,
            'source': e.source,
            'finished_at': e.created_at.isoformat() + 'Z' if e.created_at else None,
        } for e in progress_rows]
    })


# Read-only table browser allowlist. Table and column identifiers used in
# /admin/db queries come ONLY from this dict, never from the request.
ADMIN_BROWSABLE_TABLES = {
    'results': ['id', 'sailor_id', 'regatta_id', 'placement', 'boat_type', 'role',
                'points_scored', 'division', 'team_name', 'crew_partner', 'created_at'],
    'sailors': ['id', 'name', 'name_normalized', 'home_club', 'is_claimed',
                'created_at', 'updated_at'],
    'sailor_names': ['id', 'name', 'name_normalized', 'is_claimed', 'user_id',
                     'claimed_at', 'school', 'graduation_year', 'source',
                     'created_at', 'updated_at'],
    'hs_results': ['id', 'sailor_name_id', 'regatta_name', 'regatta_date', 'place',
                   'place_numeric', 'total_boats', 'position', 'division', 'school',
                   'created_at'],
    'college_results': ['id', 'sailor_name_id', 'regatta_name', 'regatta_date', 'place',
                        'place_numeric', 'total_boats', 'position', 'division', 'school',
                        'created_at'],
    'regattas': ['id', 'name', 'location', 'start_date', 'end_date', 'fleet_type',
                 'external_id', 'created_at'],
    'schools': ['id', 'name', 'district', 'source', 'url_slug', 'full_url',
                'created_at', 'updated_at'],
    'scraper_logs': ['id', 'started_at', 'completed_at', 'status', 'regattas_scraped',
                     'sailors_added', 'results_added', 'regattas_listed', 'regattas_skipped',
                     'regattas_remaining', 'regattas_empty', 'duration_seconds', 'error_message'],
    'clubspot_regatta_checks': ['external_id', 'name', 'start_date', 'end_date',
                                'last_checked_at', 'result_count', 'check_count',
                                'first_seen_at', 'last_seen_at', 'source', 'remote_created_at'],
    'scraper_state': ['key', 'value', 'updated_at'],
    'scraper_log_entries': ['id', 'created_at', 'level', 'message', 'step', 'section',
                            'season', 'source'],
    'profile_claims': ['id', 'user_id', 'sailor_id', 'sailor_name_id', 'status',
                       'is_primary', 'evidence', 'requested_at', 'decided_at',
                       'decided_by_user_id'],
    'manual_results': ['id', 'user_id', 'regatta_name', 'regatta_date', 'location',
                       'place_numeric', 'total_boats', 'boat_type', 'division', 'role',
                       'team_name', 'crew_partner', 'source_url', 'verified',
                       'created_at', 'updated_at'],
    # `users` stays off this list on purpose: it holds password hashes and
    # email-verification tokens.
}

ADMIN_DB_PAGE_SIZE = 50


@app.route('/admin/db')
@admin_required
def admin_db_browser():
    """Read-only table browser: paging and column sort only, no editing"""
    table = request.args.get('table', 'results')
    if table not in ADMIN_BROWSABLE_TABLES:
        abort(404)
    columns = ADMIN_BROWSABLE_TABLES[table]

    sort = request.args.get('sort', 'id')
    if sort not in columns:
        abort(404)

    direction = request.args.get('dir', 'desc')
    if direction not in ('asc', 'desc'):
        abort(404)

    try:
        page = max(int(request.args.get('page', 1)), 1)
    except ValueError:
        abort(404)

    col_sql = ', '.join(f'"{c}"' for c in columns)
    total = db.session.execute(text(f'SELECT count(*) FROM "{table}"')).scalar() or 0
    rows = db.session.execute(
        text(f'SELECT {col_sql} FROM "{table}" '
             f'ORDER BY "{sort}" {direction.upper()} NULLS LAST '
             f'LIMIT :limit OFFSET :offset'),
        {'limit': ADMIN_DB_PAGE_SIZE, 'offset': (page - 1) * ADMIN_DB_PAGE_SIZE}
    ).mappings().all()

    total_pages = max((total + ADMIN_DB_PAGE_SIZE - 1) // ADMIN_DB_PAGE_SIZE, 1)

    return render_template('admin_db.html',
                           tables=sorted(ADMIN_BROWSABLE_TABLES.keys()),
                           table=table,
                           columns=columns,
                           rows=rows,
                           sort=sort,
                           direction=direction,
                           page=page,
                           total_pages=total_pages,
                           total=total)


@app.route('/admin/logs/stream')
@admin_required
def admin_logs_stream():
    """
    Server-Sent Events stream of scraper_log_entries rows.

    Polls the table server-side and closes after ~50s so a worker thread is
    never held indefinitely; EventSource reconnects automatically and resumes
    from Last-Event-ID.
    """
    last_id_raw = request.headers.get('Last-Event-ID') or request.args.get('after')
    try:
        last_id = int(last_id_raw)
    except (TypeError, ValueError):
        last_id = None

    def generate(last_id):
        if last_id is None:
            # Fresh connection: start 50 lines back so the panel shows
            # recent history instead of only lines from now on.
            max_id = db.session.query(func.max(ScraperLogEntry.id)).scalar() or 0
            last_id = max(0, max_id - 50)

        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            entries = (ScraperLogEntry.query
                       .filter(ScraperLogEntry.id > last_id)
                       .order_by(ScraperLogEntry.id)
                       .limit(200)
                       .all())
            # End the read transaction so the next poll sees new commits
            # and no idle transaction sits open on Neon between polls.
            db.session.rollback()

            for entry in entries:
                last_id = entry.id
                payload = json.dumps({
                    'ts': entry.created_at.isoformat() + 'Z' if entry.created_at else None,
                    'level': entry.level,
                    'message': entry.message,
                    'step': entry.step,
                    'section': entry.section,
                    'season': entry.season,
                    'source': entry.source,
                })
                yield f"id: {entry.id}\ndata: {payload}\n\n"

            if not entries:
                yield ": keepalive\n\n"

            time.sleep(2)

    return Response(
        stream_with_context(generate(last_id)),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/api/sailors/<int:sailor_id>/stats')
def api_sailor_stats(sailor_id):
    """Get sailor statistics (for charts/graphs)"""
    sailor = Sailor.query.get_or_404(sailor_id)
    stats = calculate_stats(sailor)
    return jsonify(stats)


@app.route('/api/sailors/<int:sailor_id>/results')
def api_sailor_results(sailor_id):
    """Get all results for a sailor"""
    results = db.session.query(Result, Regatta).join(
        Regatta, Result.regatta_id == Regatta.id
    ).filter(
        Result.sailor_id == sailor_id
    ).order_by(desc(Regatta.start_date)).all()

    data = [{
        'id': r.Result.id,
        'placement': r.Result.placement,
        'boat_type': r.Result.boat_type,
        'role': r.Result.role,
        'points': r.Result.points_scored,
        'regatta': {
            'name': r.Regatta.name,
            'location': r.Regatta.location,
            'date': r.Regatta.start_date.isoformat(),
            'fleet': r.Regatta.fleet_type
        }
    } for r in results]

    return jsonify(data)


@app.route('/api/resume-link/create', methods=['POST'])
@login_required
def api_create_resume_link():
    """Create a shareable resume link owned by the current user."""
    data = request.get_json() or {}

    if not current_user.has_claim:
        return jsonify({'error': 'Claim a sailor profile first.'}), 403

    # Publishing is the point where a claim becomes public, so that is where
    # the verified-email requirement sits -- not on claiming itself.
    if (app.config['REQUIRE_VERIFIED_EMAIL_TO_PUBLISH']
            and not current_user.email_verified):
        return jsonify({
            'error': 'Confirm your email address before publishing a resume.',
            'needs_verification': True
        }), 403

    # Accept only keys the user actually owns: the selection arrives from the
    # client and must not be able to pull in someone else's results.
    owned = {e.key for group in collect_user_entries(current_user).values()
             for e in group}
    requested = data.get('selected_result_ids') or []
    selected = [k for k in requested if k in owned]

    if requested and not selected:
        return jsonify({'error': 'None of those results belong to you.'}), 403

    style = data.get('template_style', 'modern')
    if style not in ('modern', 'classic', 'minimal'):
        style = 'modern'

    primary = current_user.primary_claim

    resume_link = ResumeLink(
        user_id=current_user.id,
        # Kept populated when there is a ClubSpot claim so the old per-sailor
        # views and `Sailor.resume_links` keep working.
        sailor_id=primary.sailor_id if primary else None,
        token=ResumeLink.generate_token(),
        title=(data.get('title') or 'Sailing Resume')[:200],
        custom_bio=data.get('custom_bio'),
        selected_result_ids=selected,
        template_style=style
    )

    db.session.add(resume_link)
    db.session.commit()

    share_url = url_for('shared_resume', token=resume_link.token, _external=True)

    return jsonify({
        'success': True,
        'token': resume_link.token,
        'url': share_url,
        'result_count': len(selected)
    })


@app.route('/api/resume-link/<token>/pdf')
def api_resume_pdf(token):
    """Export resume as PDF"""
    if not app.config['ENABLE_PDF_EXPORT']:
        return jsonify({'error': 'PDF export disabled'}), 403

    resume_link = ResumeLink.query.filter_by(token=token).first_or_404()

    # Generate PDF
    pdf_path = generate_pdf(resume_link)

    # resume_link.sailor is now nullable (HS/college-only sailors have no
    # ClubSpot row), so the filename comes from the resolved display name.
    safe_name = resume_display_name(resume_link).replace(' ', '_')

    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=f"{safe_name}_sailing_resume.pdf"
    )


@app.route('/api/scraper/run', methods=['POST'])
@admin_required
def api_run_scraper():
    """
    Trigger the ClubSpot scraper (admin only).

    The scraper runs as a GitHub Actions workflow, not in this process:
    headless Chrome does not fit in the web service's memory allowance and
    running it here OOM-killed the whole site.
    """
    repo = app.config['GITHUB_REPO']
    workflow = app.config['GITHUB_WORKFLOW_FILE']
    ref = app.config['GITHUB_WORKFLOW_REF']
    token = app.config['GITHUB_WORKFLOW_TOKEN']
    actions_url = f"https://github.com/{repo}/actions/workflows/{workflow}"

    if not token:
        return jsonify({
            'error': 'GITHUB_WORKFLOW_TOKEN is not set, so this button cannot '
                     'start the workflow. Run it from the Actions tab instead.',
            'actions_url': actions_url
        }), 503

    try:
        response = requests.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
            headers={
                'Accept': 'application/vnd.github+json',
                'Authorization': f'Bearer {token}',
                'X-GitHub-Api-Version': '2022-11-28',
            },
            json={'ref': ref},
            timeout=30
        )
    except requests.RequestException as e:
        return jsonify({'error': f'Could not reach GitHub: {e}'}), 502

    # A successful dispatch returns 204 with no body
    if response.status_code == 204:
        return jsonify({
            'success': True,
            'message': f'Scrape started on GitHub Actions ({workflow} @ {ref}). '
                       f'Progress appears in the live log below and in the Actions tab.',
            'actions_url': actions_url
        })

    detail = ''
    try:
        detail = response.json().get('message', '')
    except ValueError:
        detail = response.text[:200]
    return jsonify({
        'error': f'GitHub refused the request (HTTP {response.status_code}). {detail}',
        'actions_url': actions_url
    }), 502


@app.route('/api/scraper/stop', methods=['POST'])
@admin_required
def api_stop_scraper():
    """Stop currently running scraper (admin only)"""
    try:
        from scraper import stop_scraper
        stopped = stop_scraper()

        if stopped:
            return jsonify({
                'success': True,
                'message': 'Scraper stop requested'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'No scraper currently running'
            }), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/scraper/scrape-schools', methods=['POST'])
@admin_required
def api_scrape_schools():
    """
    Step 1: Scrape all schools and store in database
    """
    try:
        data = request.get_json() or {}
        source_type = data.get('source', 'hs')  # 'hs' or 'college'

        from threading import Thread
        from scraper_schools import scrape_schools_only

        def run_with_context():
            with app.app_context():
                scrape_schools_only(source_type=source_type)

        thread = Thread(target=run_with_context)
        thread.start()

        return jsonify({
            'success': True,
            'message': f'Scraping {source_type.upper()} schools...'
        })
    except Exception as e:
        app.logger.error(f"Scrape schools error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/scraper/scrape-rosters', methods=['POST'])
@admin_required
def api_scrape_rosters():
    """
    Step 2: Scrape rosters for a specific season
    """
    try:
        data = request.get_json() or {}
        season_code = data.get('season', 'f25')  # e.g., "f25", "s24"
        source_type = data.get('source', 'hs')  # 'hs' or 'college'

        from threading import Thread
        from scraper_schools import scrape_rosters_for_season

        def run_with_context():
            with app.app_context():
                scrape_rosters_for_season(season_code=season_code, source_type=source_type)

        thread = Thread(target=run_with_context)
        thread.start()

        return jsonify({
            'success': True,
            'message': f'Scraping {source_type.upper()} rosters for {season_code.upper()}...'
        })
    except Exception as e:
        app.logger.error(f"Scrape rosters error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/scraper/scrape-results', methods=['POST'])
@admin_required
def api_scrape_results():
    """
    Step 3: Scrape results for sailors from a specific season
    """
    try:
        data = request.get_json() or {}
        season_code = data.get('season', 'f25')  # e.g., "f25", "s24"
        source_type = data.get('source', 'hs')  # 'hs' or 'college'

        from threading import Thread
        from scraper_schools import scrape_results_for_season

        def run_with_context():
            with app.app_context():
                scrape_results_for_season(season_code=season_code, source_type=source_type)

        thread = Thread(target=run_with_context)
        thread.start()

        return jsonify({
            'success': True,
            'message': f'Scraping {source_type.upper()} results for {season_code.upper()}...'
        })
    except Exception as e:
        app.logger.error(f"Scrape results error: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()

        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember_me.data)
            user.last_login = datetime.utcnow()
            db.session.commit()

            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))

        flash('Invalid email or password.', 'error')

    return render_template('login.html', form=form)


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Registration page"""
    if not app.config['ENABLE_REGISTRATION']:
        flash('Registration is currently disabled.', 'error')
        return redirect(url_for('index'))

    if current_user.is_authenticated:
        return redirect(url_for('index'))

    form = RegisterForm()
    if form.validate_on_submit():
        user = User(
            email=form.email.data.lower(),
            full_name=form.full_name.data.strip(),
            role=form.role.data
        )
        user.set_password(form.password.data)

        db.session.add(user)
        db.session.commit()

        login_user(user)

        sent, link = send_verification_email(user)
        db.session.commit()  # persist the token issued above
        if sent:
            flash('Registration successful! Check your email to confirm your '
                  'address.', 'success')
        else:
            # Email is off (or misconfigured): surface the link rather than
            # leaving the sailor stuck at an unverifiable account.
            app.logger.info('Verification link for %s: %s', user.email, link)
            flash('Registration successful! Welcome to RegattaResume.', 'success')

        # Sailors land on the claim flow; coaches have nothing to claim.
        if user.role == 'sailor':
            return redirect(url_for('claim_profile'))
        return redirect(url_for('index'))

    return render_template('register.html', form=form)


@app.route('/logout')
@login_required
def logout():
    """Logout"""
    logout_user()
    return redirect(url_for('index'))


CLAIM_SEARCH_LIMIT = 20


def _claim_search(query):
    """
    Find unclaimed profiles matching a name, across both sources.

    Server-side and limited: the old page loaded every unclaimed Sailor row
    and filtered in JavaScript, which is tens of thousands of rows once the
    rosters are scraped. Tokenized the same way as /search so "John Smith"
    still finds "Smith, John".
    """
    tokens = [t for t in query.lower().split() if t]
    if not tokens:
        return [], []

    sailor_q = Sailor.query.filter_by(is_claimed=False)
    name_q = SailorName.query.filter_by(is_claimed=False)
    for token in tokens:
        sailor_q = sailor_q.filter(Sailor.name_normalized.contains(token))
        name_q = name_q.filter(SailorName.name_normalized.contains(token))

    return (sailor_q.order_by(Sailor.name).limit(CLAIM_SEARCH_LIMIT).all(),
            name_q.order_by(SailorName.name).limit(CLAIM_SEARCH_LIMIT).all())


def _suggested_matches(user):
    """
    Other-source profiles whose name matches something already claimed.

    A sailor is normally several rows -- a ClubSpot profile and one or more
    HS/college names. Surfacing the rest is what turns a claim into a complete
    resume instead of a half one.
    """
    claimed_normals = {s.name_normalized for s in user.claimed_sailors if s.name_normalized}
    claimed_normals |= {n.name_normalized for n in user.claimed_names if n.name_normalized}
    if not claimed_normals:
        return [], []

    already_sailors = {s.id for s in user.claimed_sailors}
    already_names = {n.id for n in user.claimed_names}

    sailors = [
        s for s in Sailor.query.filter(
            Sailor.name_normalized.in_(claimed_normals),
            Sailor.is_claimed == False  # noqa: E712 - SQL, not Python
        ).limit(CLAIM_SEARCH_LIMIT).all()
        if s.id not in already_sailors
    ]
    names = [
        n for n in SailorName.query.filter(
            SailorName.name_normalized.in_(claimed_normals),
            SailorName.is_claimed == False  # noqa: E712
        ).limit(CLAIM_SEARCH_LIMIT).all()
        if n.id not in already_names
    ]
    return sailors, names


@app.route('/claim-profile', methods=['GET', 'POST'])
@login_required
def claim_profile():
    """
    Claim a scraped profile -- either a ClubSpot Sailor or an HS/College
    SailorName.

    Claiming is additive: a sailor can hold several profiles, so this page
    stays reachable after the first claim.
    """
    form = ClaimProfileForm()

    if form.validate_on_submit():
        return _handle_claim(form)

    query = request.args.get('q', '').strip()
    # Default the search box to the name on the account -- usually the sailor
    # is looking for exactly that.
    if not query and not current_user.has_claim and current_user.full_name:
        query = current_user.full_name

    sailors, sailor_names = _claim_search(query) if query else ([], [])
    suggested_sailors, suggested_names = _suggested_matches(current_user)

    return render_template('claim_profile.html',
                         form=form,
                         query=query,
                         sailors=sailors,
                         sailor_names=sailor_names,
                         suggested_sailors=suggested_sailors,
                         suggested_names=suggested_names,
                         claims=current_user.approved_claims)


def _handle_claim(form):
    """Apply one claim submitted from the claim page."""
    evidence = (form.evidence.data or '').strip() or None
    is_first = not current_user.has_claim

    if form.sailor_id.data:
        kind, target = CLAIM_CLUBSPOT, Sailor.query.get(form.sailor_id.data)
    else:
        kind, target = CLAIM_SAILOR_NAME, SailorName.query.get(form.sailor_name_id.data)

    # get() rather than get_or_404 earlier returned None and 500'd on a forged id
    if target is None:
        flash('That profile no longer exists.', 'error')
        return redirect(url_for('claim_profile'))

    if target.is_claimed:
        flash('This profile has already been claimed. If it is yours, '
              'contact us and we will sort it out.', 'error')
        return redirect(url_for('claim_profile'))

    claim = ProfileClaim(
        user_id=current_user.id,
        status='approved',
        is_primary=is_first,
        evidence=evidence,
        decided_at=datetime.utcnow()
    )
    if kind == CLAIM_CLUBSPOT:
        claim.sailor_id = target.id
    else:
        claim.sailor_name_id = target.id
        # Keep the legacy columns in step with the new claim table.
        target.user_id = current_user.id
        target.claimed_at = datetime.utcnow()

    target.is_claimed = True

    # Mirror into the deprecated single-profile FK so a rollback to the
    # pre-008 code still finds the claim. Migration 011 removes this.
    if kind == CLAIM_CLUBSPOT and current_user.sailor_id is None:
        current_user.sailor_id = target.id

    if not current_user.full_name:
        current_user.full_name = target.name

    db.session.add(claim)
    db.session.commit()

    send_claim_notification(current_user, claim)

    flash(f'Claimed {target.name}. Add any missing results, then build your '
          f'resume.', 'success')
    return redirect(url_for('claim_profile'))


@app.route('/claim-profile/<int:claim_id>/release', methods=['POST'])
@login_required
def release_claim(claim_id):
    """
    Give up a claim -- the "this isn't me" path.

    Self-serve and reversible by design: without it, a wrong claim needs an
    admin, and the profile stays locked in the meantime.
    """
    claim = ProfileClaim.query.get_or_404(claim_id)
    if claim.user_id != current_user.id:
        abort(403)

    name = claim.profile_name
    was_primary = claim.is_primary

    if claim.sailor:
        claim.sailor.is_claimed = False
        if current_user.sailor_id == claim.sailor_id:
            current_user.sailor_id = None
    if claim.sailor_name:
        claim.sailor_name.is_claimed = False
        claim.sailor_name.user_id = None
        claim.sailor_name.claimed_at = None

    db.session.delete(claim)
    db.session.flush()

    # Promote another claim so the resume keeps a canonical name.
    if was_primary:
        remaining = [c for c in current_user.claims if c.id != claim_id
                     and c.status == 'approved']
        if remaining:
            remaining[0].is_primary = True

    db.session.commit()
    flash(f'Released {name}.', 'info')
    return redirect(url_for('claim_profile'))


# ============================================================================
# ACCOUNT
# ============================================================================

@app.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    """Edit the name, email and school stored on the account."""
    form = AccountForm(user=current_user, obj=current_user)

    if form.validate_on_submit():
        new_email = form.email.data.lower().strip()
        email_changed = new_email != current_user.email

        current_user.full_name = form.full_name.data.strip()
        current_user.display_name = (form.display_name.data or '').strip() or None
        current_user.grad_year = form.grad_year.data
        current_user.primary_school = (form.primary_school.data or '').strip() or None
        current_user.email = new_email
        current_user.profile_completed_at = datetime.utcnow()

        if email_changed:
            # A new address is unproven, whatever the old one was.
            current_user.email_verified = False
            sent, link = send_verification_email(current_user)
            db.session.commit()
            if sent:
                flash('Saved. Check your new address for a confirmation link.',
                      'success')
            else:
                app.logger.info('Verification link for %s: %s',
                                current_user.email, link)
                flash('Saved. Email sending is off, so your address stays '
                      'unconfirmed.', 'warning')
        else:
            db.session.commit()
            flash('Account updated.', 'success')

        return redirect(url_for('account'))

    return render_template('account.html',
                         form=form,
                         claims=current_user.approved_claims,
                         manual_count=ManualResult.query.filter_by(
                             user_id=current_user.id).count(),
                         resume_links=ResumeLink.query.filter_by(
                             user_id=current_user.id
                         ).order_by(desc(ResumeLink.created_at)).all())


@app.route('/verify-email/<token>')
def verify_email(token):
    """Confirm an email address from the link we mailed."""
    user = User.query.filter_by(email_verify_token=token).first()

    if user is None:
        flash('That confirmation link is not valid. Request a new one.', 'error')
        return redirect(url_for('account') if current_user.is_authenticated
                        else url_for('login'))

    max_age = timedelta(hours=app.config['EMAIL_VERIFY_TOKEN_MAX_AGE_HOURS'])
    if user.email_verify_sent_at and datetime.utcnow() - user.email_verify_sent_at > max_age:
        flash('That confirmation link has expired. Request a new one.', 'error')
        return redirect(url_for('account') if current_user.is_authenticated
                        else url_for('login'))

    user.email_verified = True
    user.email_verify_token = None  # single use
    db.session.commit()

    flash('Email confirmed. You can publish a resume now.', 'success')
    return redirect(url_for('account') if current_user.is_authenticated
                    else url_for('login'))


@app.route('/account/resend-verification', methods=['POST'])
@login_required
def resend_verification():
    """Issue a fresh confirmation email."""
    if current_user.email_verified:
        flash('Your email is already confirmed.', 'info')
        return redirect(url_for('account'))

    sent, link = send_verification_email(current_user)
    db.session.commit()

    if sent:
        flash('Confirmation email sent.', 'success')
    else:
        app.logger.info('Verification link for %s: %s', current_user.email, link)
        flash('Email sending is disabled on this deployment; the confirmation '
              'link is in the server log.', 'warning')

    return redirect(url_for('account'))


# ============================================================================
# SELF-ADDED RESULTS
# ============================================================================

@app.route('/account/results')
@login_required
def manual_results():
    """List the results this sailor added themselves."""
    rows = ManualResult.query.filter_by(user_id=current_user.id).order_by(
        desc(ManualResult.regatta_date)).all()
    return render_template('manual_results.html', results=rows)


def _possible_duplicates(user, form, exclude_id=None):
    """
    Scraped results that look like the one being entered.

    Without this, resumes fill with doubles as the scraper backfills: the
    sailor adds a regatta we have not scraped yet, and we scrape it later.
    """
    name = (form.regatta_name.data or '').strip().lower()
    if not name:
        return []

    tokens = [t for t in name.split() if len(t) > 3][:3]
    if not tokens:
        return []

    grouped = collect_user_entries(user)
    candidates = []
    for kind in (KIND_CLUBSPOT, KIND_HS, KIND_COLLEGE, KIND_MANUAL):
        for entry in grouped.get(kind, []):
            if exclude_id and entry.key == f'{KIND_MANUAL}:{exclude_id}':
                continue
            existing = entry.regatta_name.lower()
            if all(t in existing for t in tokens):
                if form.regatta_date.data and entry.date:
                    # Same event, scraped under a slightly different name:
                    # allow a few days' drift between listing and sailing.
                    if abs((entry.date - form.regatta_date.data).days) > 10:
                        continue
                candidates.append(entry)
    return candidates[:5]


@app.route('/account/results/new', methods=['GET', 'POST'])
@login_required
def manual_result_new():
    """Add a result the scrapers missed."""
    redirect_response = require_claim_or_redirect()
    if redirect_response:
        return redirect_response

    form = ManualResultForm()
    duplicates = []

    if form.validate_on_submit():
        duplicates = _possible_duplicates(current_user, form)
        # Warn once, then take it on the next submit if they insist.
        if duplicates and not request.form.get('confirm_duplicate'):
            flash('We may already have this regatta. Check below, then save '
                  'again to add it anyway.', 'warning')
        else:
            row = ManualResult(user_id=current_user.id)
            _apply_manual_result(form, row)
            db.session.add(row)
            db.session.commit()
            flash('Result added.', 'success')
            return redirect(url_for('manual_results'))

    return render_template('manual_result_form.html',
                         form=form, result=None, duplicates=duplicates)


@app.route('/account/results/<int:result_id>/edit', methods=['GET', 'POST'])
@login_required
def manual_result_edit(result_id):
    """Edit one of the user's own added results."""
    row = ManualResult.query.get_or_404(result_id)
    if row.user_id != current_user.id:
        abort(403)

    form = ManualResultForm(obj=row)
    duplicates = []

    if form.validate_on_submit():
        _apply_manual_result(form, row)
        db.session.commit()
        flash('Result updated.', 'success')
        return redirect(url_for('manual_results'))

    return render_template('manual_result_form.html',
                         form=form, result=row, duplicates=duplicates)


@app.route('/account/results/<int:result_id>/delete', methods=['POST'])
@login_required
def manual_result_delete(result_id):
    """Delete one of the user's own added results."""
    row = ManualResult.query.get_or_404(result_id)
    if row.user_id != current_user.id:
        abort(403)

    db.session.delete(row)
    db.session.commit()
    flash('Result deleted.', 'info')
    return redirect(url_for('manual_results'))


def _apply_manual_result(form, row):
    """Copy form fields onto a ManualResult, normalizing blanks to NULL."""
    row.regatta_name = form.regatta_name.data.strip()
    row.regatta_date = form.regatta_date.data
    row.location = (form.location.data or '').strip() or None
    row.place_numeric = form.place_numeric.data
    row.total_boats = form.total_boats.data
    row.boat_type = (form.boat_type.data or '').strip() or None
    row.division = (form.division.data or '').strip() or None
    row.role = form.role.data or None
    row.team_name = (form.team_name.data or '').strip() or None
    row.crew_partner = (form.crew_partner.data or '').strip() or None
    row.source_url = (form.source_url.data or '').strip() or None
    row.notes = (form.notes.data or '').strip() or None
    # An edit by the sailor re-opens the question of whether it is confirmed.
    row.verified = False


# ============================================================================
# ADMIN: CLAIMS
# ============================================================================

@app.route('/admin/claims')
@admin_required
def admin_claims():
    """Review claims and release wrong ones."""
    claims = ProfileClaim.query.order_by(desc(ProfileClaim.requested_at)).limit(200).all()
    return render_template('admin_claims.html', claims=claims)


@app.route('/admin/claims/<int:claim_id>/revoke', methods=['POST'])
@admin_required
def admin_revoke_claim(claim_id):
    """Unclaim a profile on a sailor's behalf (the dispute path)."""
    claim = ProfileClaim.query.get_or_404(claim_id)
    name = claim.profile_name

    if claim.sailor:
        claim.sailor.is_claimed = False
    if claim.sailor_name:
        claim.sailor_name.is_claimed = False
        claim.sailor_name.user_id = None
        claim.sailor_name.claimed_at = None

    owner = claim.user
    if owner and owner.sailor_id == claim.sailor_id:
        owner.sailor_id = None

    db.session.delete(claim)
    db.session.commit()

    flash(f'Revoked the claim on {name}.', 'info')
    return redirect(url_for('admin_claims'))


@app.route('/searchbar')
def searchbar():
    """Blank searchbar page for custom development"""
    return render_template('searchbar.html')


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403


@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500


# ============================================================================
# CLI COMMANDS
# ============================================================================

@app.cli.command()
def init_db():
    """Initialize the database"""
    db.create_all()
    print("Database initialized!")


@app.cli.command('create-admin')
@click.argument('email')
@click.option('--password', prompt=True, hide_input=True,
              help='Omit to be prompted. Prefer the prompt or stdin over an '
                   'argument, which leaks into shell history and `ps` output.')
@click.option('--name', default=None, help='Full name for the resume header.')
def create_admin(email, password, name):
    """
    Create an admin account, or promote and reset an existing one.

    Idempotent, so it is safe to re-run:

        flask create-admin someone@example.com

    The account is marked email-verified, since an admin should not be waiting
    on a confirmation mail that a sendless deployment never delivers.
    """
    email = email.lower().strip()
    if len(password) < 8:
        print('Refusing: password must be at least 8 characters '
              '(the registration form enforces the same minimum).')
        return

    user = User.query.filter_by(email=email).first()
    created = user is None

    if created:
        user = User(email=email, role='sailor')
        db.session.add(user)

    user.set_password(password)
    user.is_admin = True
    user.email_verified = True
    user.email_verify_token = None
    if name:
        user.full_name = name
    elif not user.full_name:
        user.full_name = email.split('@')[0]

    db.session.commit()
    print(f"{'Created' if created else 'Updated'} admin {user.email} "
          f"(id={user.id}, name={user.full_name!r}).")


@app.cli.command('make-admin')
@click.argument('email')
def make_admin(email):
    """
    Grant admin rights to a user by email.

    /admin and the scraper controls are admin-only now, and is_admin defaults
    to false for everyone -- including existing accounts -- so the first admin
    has to be promoted from here:

        flask make-admin you@example.com
    """
    user = User.query.filter_by(email=email.lower()).first()
    if user is None:
        print(f"No user with email {email!r}.")
        return

    user.is_admin = True
    db.session.commit()
    print(f"{user.email} is now an admin.")


@app.cli.command('verify-email-manually')
@click.argument('email')
def verify_email_manually(email):
    """Mark an address confirmed without sending mail (for deployments with
    ENABLE_EMAIL=false)."""
    user = User.query.filter_by(email=email.lower()).first()
    if user is None:
        print(f"No user with email {email!r}.")
        return

    user.email_verified = True
    user.email_verify_token = None
    db.session.commit()
    print(f"{user.email} is now verified.")


@app.cli.command()
@click.option('--limit', type=int, default=None,
              help='Max regattas to visit this run (default: all that are due)')
@click.option('--start-year', type=int, default=None,
              help='Only scrape regattas from this year onwards (default: no floor)')
def scrape(limit, start_year):
    """Run the ClubSpot scraper (entry point for the GitHub Actions job)"""
    from scraper import run_scraper
    print(f"Starting scraper (limit={limit or 'none'}, start_year={start_year or 'none'})...")
    stats = run_scraper(limit=limit, start_year=start_year)
    print(f"Scraping complete! Stats: {stats}")


@app.cli.command('import-regatta-ids')
@click.argument('path', type=click.Path(exists=True, dir_okay=False))
@click.option('--source', default='sitemap', help="Where the IDs came from (default: sitemap)")
def import_regatta_ids(path, source):
    """Merge ClubSpot regatta IDs (one per line, or regatta URLs) into the catalogue"""
    import re
    import clubspot_catalog as catalog
    ids = set()
    with open(path) as f:
        for line in f:
            match = re.search(r'regatta/([A-Za-z0-9]{6,20})', line) or re.fullmatch(r'\s*([A-Za-z0-9]{6,20})\s*', line)
            if match:
                ids.add(match.group(1))
    added = catalog.upsert_ids(ids, source=source)
    print(f"{len(ids)} IDs read from {path}; {added} new rows added to the catalogue")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)
