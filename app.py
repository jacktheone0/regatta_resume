from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_migrate import Migrate
from apscheduler.schedulers.background import BackgroundScheduler
from config import config
from models import db, User, Sailor, Regatta, Result, ResumeLink, SailorName, HSResult, CollegeResult
from forms import LoginForm, RegisterForm, ClaimProfileForm
from scraper import run_scraper
from utils import generate_pdf, calculate_stats, get_performance_trends
import os
from datetime import datetime, timedelta
from sqlalchemy import desc, func

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
# SCHEDULER SETUP - Scraper runs every Sunday at 11:59 PM
# ============================================================================

def scheduled_scraper_job():
    """Scraper job that runs on schedule"""
    with app.app_context():
        try:
            app.logger.info("Starting scheduled scraper job...")
            run_scraper()
            app.logger.info("Scheduled scraper job completed successfully")
        except Exception as e:
            app.logger.error(f"Scheduled scraper job failed: {e}")

if app.config['SCRAPER_ENABLED']:
    scheduler = BackgroundScheduler(timezone=app.config['SCHEDULER_TIMEZONE'])
    # Run every Sunday at 23:59 (11:59 PM)
    scheduler.add_job(
        scheduled_scraper_job,
        'cron',
        day_of_week='sun',
        hour=23,
        minute=59,
        id='weekly_scraper'
    )
    scheduler.start()
    app.logger.info("Scheduler started: Scraper will run every Sunday at 11:59 PM")


# ============================================================================
# PUBLIC ROUTES
# ============================================================================

@app.route('/')
def index():
    """Landing page with search"""
    recent_sailors = Sailor.query.order_by(desc(Sailor.updated_at)).limit(10).all()
    total_sailors = Sailor.query.count()
    total_regattas = Regatta.query.count()

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

    results = []

    # Search in Sailor table (ClubSpot data)
    sailors = Sailor.query.filter(
        Sailor.name_normalized.contains(query.lower())
    ).limit(10).all()

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
    sailor_names = SailorName.query.filter(
        SailorName.name_normalized.contains(query.lower())
    ).limit(10).all()

    for sn in sailor_names:
        # Count total results
        hs_count = HSResult.query.filter_by(sailor_name_id=sn.id).count()
        college_count = CollegeResult.query.filter_by(sailor_name_id=sn.id).count()
        total_results = hs_count + college_count

        # Get best finish
        best_hs = db.session.query(db.func.min(HSResult.place_numeric)).filter_by(sailor_name_id=sn.id).scalar()
        best_college = db.session.query(db.func.min(CollegeResult.place_numeric)).filter_by(sailor_name_id=sn.id).scalar()
        best_finish = None
        if best_hs and best_college:
            best_finish = min(best_hs, best_college)
        elif best_hs:
            best_finish = best_hs
        elif best_college:
            best_finish = best_college

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
    is_owner = current_user.is_authenticated and current_user.sailor_id == sailor_id

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
    is_owner = current_user.is_authenticated and sailor_name.user_id == current_user.id

    return render_template('sailor_name_profile.html',
                         sailor_name=sailor_name,
                         hs_results=hs_results,
                         college_results=college_results,
                         stats=stats,
                         is_owner=is_owner)


@app.route('/sailor/<int:sailor_id>/resume-builder')
@login_required
def resume_builder(sailor_id):
    """Resume builder page - only for profile owners"""
    sailor = Sailor.query.get_or_404(sailor_id)

    # Check ownership
    if current_user.sailor_id != sailor_id:
        flash('You can only edit your own resume.', 'error')
        return redirect(url_for('sailor_profile', sailor_id=sailor_id))

    # Get all results
    results = db.session.query(Result, Regatta).join(
        Regatta, Result.regatta_id == Regatta.id
    ).filter(
        Result.sailor_id == sailor_id
    ).order_by(
        desc(Regatta.start_date)
    ).all()

    return render_template('resume_builder.html',
                         sailor=sailor,
                         results=results)


@app.route('/coach-view/<int:sailor_id>')
def coach_view(sailor_id):
    """Coach analytics view for a sailor"""
    sailor = Sailor.query.get_or_404(sailor_id)

    # Get configurable time range from query params
    months_back = int(request.args.get('months', 6))  # Default: 6 months
    cutoff_date = datetime.utcnow().date() - timedelta(days=months_back * 30)

    # Get recent vs historical results
    recent_results = db.session.query(Result, Regatta).join(
        Regatta, Result.regatta_id == Regatta.id
    ).filter(
        Result.sailor_id == sailor_id,
        Regatta.start_date >= cutoff_date
    ).order_by(desc(Regatta.start_date)).all()

    historical_results = db.session.query(Result, Regatta).join(
        Regatta, Result.regatta_id == Regatta.id
    ).filter(
        Result.sailor_id == sailor_id,
        Regatta.start_date < cutoff_date
    ).order_by(desc(Regatta.start_date)).all()

    # Calculate performance trends
    trends = get_performance_trends(sailor_id, months_back)

    # Get fleet breakdown
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
                         months_back=months_back)


@app.route('/resume/<token>')
def shared_resume(token):
    """View a shared resume link"""
    resume_link = ResumeLink.query.filter_by(token=token).first_or_404()

    # Update access stats
    resume_link.last_accessed = datetime.utcnow()
    resume_link.access_count += 1
    db.session.commit()

    sailor = resume_link.sailor

    # Get selected results
    if resume_link.selected_result_ids:
        results = db.session.query(Result, Regatta).join(
            Regatta, Result.regatta_id == Regatta.id
        ).filter(
            Result.id.in_(resume_link.selected_result_ids)
        ).order_by(desc(Regatta.start_date)).all()
    else:
        # If no selection, show all
        results = db.session.query(Result, Regatta).join(
            Regatta, Result.regatta_id == Regatta.id
        ).filter(
            Result.sailor_id == sailor.id
        ).order_by(desc(Regatta.start_date)).all()

    return render_template(f'resume_{resume_link.template_style}.html',
                         sailor=sailor,
                         resume_link=resume_link,
                         results=results)


@app.route('/admin')
@login_required
def admin_dashboard():
    """Admin dashboard with scraper controls"""
    # Calculate database stats
    stats = {
        'total_sailors': Sailor.query.count(),
        'total_regattas': Regatta.query.count(),
        'total_results': Result.query.count()
    }

    return render_template('admin.html', stats=stats)


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
    """Create a shareable resume link"""
    data = request.get_json()
    sailor_id = data.get('sailor_id')

    # Verify ownership
    if current_user.sailor_id != sailor_id:
        return jsonify({'error': 'Unauthorized'}), 403

    # Create resume link
    resume_link = ResumeLink(
        sailor_id=sailor_id,
        token=ResumeLink.generate_token(),
        title=data.get('title', 'Sailing Resume'),
        custom_bio=data.get('custom_bio'),
        selected_result_ids=data.get('selected_result_ids'),
        template_style=data.get('template_style', 'modern')
    )

    db.session.add(resume_link)
    db.session.commit()

    share_url = url_for('shared_resume', token=resume_link.token, _external=True)

    return jsonify({
        'success': True,
        'token': resume_link.token,
        'url': share_url
    })


@app.route('/api/resume-link/<token>/pdf')
def api_resume_pdf(token):
    """Export resume as PDF"""
    if not app.config['ENABLE_PDF_EXPORT']:
        return jsonify({'error': 'PDF export disabled'}), 403

    resume_link = ResumeLink.query.filter_by(token=token).first_or_404()
    sailor = resume_link.sailor

    # Generate PDF
    pdf_path = generate_pdf(resume_link)

    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=f"{sailor.name.replace(' ', '_')}_sailing_resume.pdf"
    )


@app.route('/api/scraper/run', methods=['POST'])
@login_required
def api_run_scraper():
    """Manually trigger scraper (admin only)"""
    # In production, add admin check here
    # if not current_user.is_admin:
    #     return jsonify({'error': 'Admin only'}), 403

    try:
        # Run scraper in background with app context
        from threading import Thread

        def run_with_context():
            with app.app_context():
                run_scraper()

        thread = Thread(target=run_with_context)
        thread.start()

        return jsonify({
            'success': True,
            'message': 'Scraper started in background'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/scraper/stop', methods=['POST'])
@login_required
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


@app.route('/api/scraper/run-hs-schools', methods=['POST'])
@login_required
def api_run_hs_schools_scraper():
    """
    Run High School sailing scraper (schools-based approach)
    Scrapes all schools to get sailor names, then scrapes results for each
    """
    try:
        data = request.get_json() or {}
        limit_schools = data.get('limit_schools', 5)  # Default: 5 schools for testing
        limit_sailors = data.get('limit_sailors', 50)  # Default: 50 sailors

        from threading import Thread
        from scraper_schools import run_full_hs_scraper

        def run_with_context():
            with app.app_context():
                run_full_hs_scraper(
                    limit_schools=limit_schools,
                    limit_sailors=limit_sailors
                )

        thread = Thread(target=run_with_context)
        thread.start()

        return jsonify({
            'success': True,
            'message': f'HS Schools scraper started (max {limit_schools} schools, {limit_sailors} sailors)'
        })
    except Exception as e:
        app.logger.error(f"HS Schools scraper error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/scraper/run-college-schools', methods=['POST'])
@login_required
def api_run_college_schools_scraper():
    """
    Run College sailing scraper (schools-based approach)
    Scrapes all schools to get sailor names, then scrapes results for each
    """
    try:
        data = request.get_json() or {}
        limit_schools = data.get('limit_schools', 5)  # Default: 5 schools for testing
        limit_sailors = data.get('limit_sailors', 50)  # Default: 50 sailors

        from threading import Thread
        from scraper_schools import run_full_college_scraper

        def run_with_context():
            with app.app_context():
                run_full_college_scraper(
                    limit_schools=limit_schools,
                    limit_sailors=limit_sailors
                )

        thread = Thread(target=run_with_context)
        thread.start()

        return jsonify({
            'success': True,
            'message': f'College Schools scraper started (max {limit_schools} schools, {limit_sailors} sailors)'
        })
    except Exception as e:
        app.logger.error(f"College Schools scraper error: {e}")
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
            role=form.role.data
        )
        user.set_password(form.password.data)

        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash('Registration successful! Welcome to RegattaResume.', 'success')
        return redirect(url_for('index'))

    return render_template('register.html', form=form)


@app.route('/logout')
@login_required
def logout():
    """Logout"""
    logout_user()
    return redirect(url_for('index'))


@app.route('/claim-profile', methods=['GET', 'POST'])
@login_required
def claim_profile():
    """Claim a sailor profile"""
    if current_user.sailor_id:
        flash('You have already claimed a profile.', 'info')
        return redirect(url_for('sailor_profile', sailor_id=current_user.sailor_id))

    form = ClaimProfileForm()

    if form.validate_on_submit():
        sailor = Sailor.query.get(form.sailor_id.data)

        if sailor.is_claimed:
            flash('This profile has already been claimed.', 'error')
        else:
            current_user.sailor_id = sailor.id
            sailor.is_claimed = True
            db.session.commit()

            flash(f'Successfully claimed profile for {sailor.name}!', 'success')
            return redirect(url_for('sailor_profile', sailor_id=sailor.id))

    # Show available unclaimed profiles
    unclaimed_sailors = Sailor.query.filter_by(is_claimed=False).order_by(Sailor.name).all()

    return render_template('claim_profile.html', form=form, sailors=unclaimed_sailors)


# ============================================================================
# ERROR HANDLERS
# ============================================================================

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


@app.cli.command()
def scrape():
    """Run the scraper manually"""
    print("Starting scraper...")
    stats = run_scraper()
    print(f"Scraping complete! Stats: {stats}")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)
