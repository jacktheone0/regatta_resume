from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import secrets

db = SQLAlchemy()

# The two kinds of scraped profile a sailor can claim. A real sailor is often
# both: a ClubSpot `Sailor` row plus one or more HS/College `SailorName` rows.
CLAIM_CLUBSPOT = 'clubspot'
CLAIM_SAILOR_NAME = 'sailor_name'


class User(UserMixin, db.Model):
    """User accounts (optional login for sailors/coaches)"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='sailor')  # 'sailor' or 'coach'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    # Identity the sailor tells us about themselves. The scraped profiles carry
    # a name too, but it is whatever the regatta organizers typed; this is the
    # spelling the sailor wants on a resume.
    full_name = db.Column(db.String(200))
    display_name = db.Column(db.String(200))   # optional public override
    grad_year = db.Column(db.Integer)
    primary_school = db.Column(db.String(200))
    profile_completed_at = db.Column(db.DateTime)

    # Email verification. A claim is usable immediately, but publishing a
    # resume requires a verified address (see ProfileClaim).
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    email_verify_token = db.Column(db.String(64), index=True)
    email_verify_sent_at = db.Column(db.DateTime)

    is_admin = db.Column(db.Boolean, nullable=False, default=False)

    # DEPRECATED: single-profile claim, superseded by ProfileClaim. Kept so a
    # mid-deploy rollback still works; migration 011 drops it.
    sailor_id = db.Column(db.Integer, db.ForeignKey('sailors.id'), nullable=True)
    sailor = db.relationship('Sailor', back_populates='user', foreign_keys=[sailor_id])

    claims = db.relationship('ProfileClaim', back_populates='user',
                             foreign_keys='ProfileClaim.user_id',
                             cascade='all, delete-orphan')
    manual_results = db.relationship('ManualResult', back_populates='user',
                                     cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def new_email_verify_token(self):
        """Issue a fresh verification token and stamp the send time."""
        self.email_verify_token = secrets.token_urlsafe(32)
        self.email_verify_sent_at = datetime.utcnow()
        return self.email_verify_token

    # ---- claims -----------------------------------------------------------

    @property
    def approved_claims(self):
        return [c for c in self.claims if c.status == 'approved']

    @property
    def claimed_sailors(self):
        """ClubSpot Sailor rows this user has claimed."""
        return [c.sailor for c in self.approved_claims if c.sailor is not None]

    @property
    def claimed_names(self):
        """HS/College SailorName rows this user has claimed."""
        return [c.sailor_name for c in self.approved_claims if c.sailor_name is not None]

    @property
    def has_claim(self):
        return bool(self.approved_claims)

    @property
    def primary_claim(self):
        """The claim that supplies the canonical display name."""
        approved = self.approved_claims
        if not approved:
            return None
        for claim in approved:
            if claim.is_primary:
                return claim
        return approved[0]

    def owns(self, kind, obj_id):
        """True if this user holds an approved claim on (kind, obj_id)."""
        if kind == CLAIM_CLUBSPOT:
            return any(c.sailor_id == obj_id for c in self.approved_claims)
        if kind == CLAIM_SAILOR_NAME:
            return any(c.sailor_name_id == obj_id for c in self.approved_claims)
        return False

    @property
    def resume_name(self):
        """Name to print on a resume, best source first."""
        if self.display_name:
            return self.display_name
        if self.full_name:
            return self.full_name
        claim = self.primary_claim
        if claim:
            return claim.profile_name
        return self.email.split('@')[0]

    def __repr__(self):
        return f'<User {self.email}>'


class Sailor(db.Model):
    """Sailor profiles"""
    __tablename__ = 'sailors'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    name_normalized = db.Column(db.String(200), index=True)  # lowercase for searching
    home_club = db.Column(db.String(200))
    bio = db.Column(db.Text)
    profile_image_url = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Claimed profile. `is_claimed` is denormalized from ProfileClaim so the
    # templates and the claim-search query stay cheap; ProfileClaim is the
    # source of truth and writes this flag on approval.
    is_claimed = db.Column(db.Boolean, default=False)
    user = db.relationship('User', back_populates='sailor', foreign_keys=[User.sailor_id])
    claim = db.relationship('ProfileClaim', back_populates='sailor', uselist=False)

    # Relationships
    results = db.relationship('Result', back_populates='sailor', cascade='all, delete-orphan')
    resume_links = db.relationship('ResumeLink', back_populates='sailor', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Sailor {self.name}>'

    @property
    def total_regattas(self):
        """Total number of unique regattas"""
        return db.session.query(db.func.count(db.distinct(Result.regatta_id))).filter(
            Result.sailor_id == self.id
        ).scalar() or 0

    @property
    def best_finish(self):
        """Best placement ever"""
        result = db.session.query(db.func.min(Result.placement)).filter(
            Result.sailor_id == self.id
        ).scalar()
        return result if result else None

    @property
    def average_placement(self):
        """Average placement across all regattas"""
        result = db.session.query(db.func.avg(Result.placement)).filter(
            Result.sailor_id == self.id
        ).scalar()
        return round(result, 1) if result else None


class ProfileClaim(db.Model):
    """
    A user's claim on one scraped profile.

    Replaces the old single `User.sailor_id` link. A sailor normally appears in
    several scraped tables under slightly different names, so claiming has to be
    many-to-one: one account, many profiles. Exactly one of `sailor_id` /
    `sailor_name_id` is set, and each profile can be claimed only once.
    """
    __tablename__ = 'profile_claims'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    sailor_id = db.Column(db.Integer, db.ForeignKey('sailors.id'), nullable=True)
    sailor_name_id = db.Column(db.Integer, db.ForeignKey('sailor_names.id'), nullable=True)

    status = db.Column(db.String(20), nullable=False, default='approved')  # pending|approved|rejected
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    evidence = db.Column(db.Text)  # school email, grad year, free-text note

    requested_at = db.Column(db.DateTime, default=datetime.utcnow)
    decided_at = db.Column(db.DateTime)
    decided_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    user = db.relationship('User', back_populates='claims', foreign_keys=[user_id])
    decided_by = db.relationship('User', foreign_keys=[decided_by_user_id])
    sailor = db.relationship('Sailor', back_populates='claim')
    sailor_name = db.relationship('SailorName', back_populates='claim')

    __table_args__ = (
        db.UniqueConstraint('sailor_id', name='uq_claim_sailor'),
        db.UniqueConstraint('sailor_name_id', name='uq_claim_sailor_name'),
        db.CheckConstraint(
            '(sailor_id IS NULL) != (sailor_name_id IS NULL)',
            name='ck_claim_one_target'
        ),
    )

    @property
    def kind(self):
        return CLAIM_CLUBSPOT if self.sailor_id else CLAIM_SAILOR_NAME

    @property
    def target_id(self):
        return self.sailor_id if self.sailor_id else self.sailor_name_id

    @property
    def profile_name(self):
        if self.sailor:
            return self.sailor.name
        if self.sailor_name:
            return self.sailor_name.name
        return None

    def __repr__(self):
        return f'<ProfileClaim user={self.user_id} {self.kind}:{self.target_id} {self.status}>'


class ManualResult(db.Model):
    """
    A result the sailor entered themselves, for regattas the scrapers missed.

    Owned by the user rather than by a claimed profile: it is a claim about the
    person, who may hold several profiles, and it must survive a profile being
    re-scraped. Deliberately never written into `results` / `hs_results` /
    `college_results` -- the scrapers own those tables and would clobber it, or
    worse, serve it to other users as scraped fact.
    """
    __tablename__ = 'manual_results'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    regatta_name = db.Column(db.String(300), nullable=False)
    regatta_date = db.Column(db.Date, index=True)
    location = db.Column(db.String(200))

    place_numeric = db.Column(db.Integer)
    total_boats = db.Column(db.Integer)
    boat_type = db.Column(db.String(100))   # "420", "Laser"
    division = db.Column(db.String(50))
    role = db.Column(db.String(20))         # 'skipper' or 'crew'
    team_name = db.Column(db.String(200))
    crew_partner = db.Column(db.String(200))
    notes = db.Column(db.Text)

    source_url = db.Column(db.String(500))  # link to published results, if any
    verified = db.Column(db.Boolean, nullable=False, default=False)  # admin-confirmed

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', back_populates='manual_results')

    def __repr__(self):
        return f'<ManualResult {self.regatta_name} ({self.user_id})>'


class Regatta(db.Model):
    """Regatta events"""
    __tablename__ = 'regattas'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(300), nullable=False)
    location = db.Column(db.String(200))
    start_date = db.Column(db.Date, nullable=False, index=True)
    end_date = db.Column(db.Date)
    fleet_type = db.Column(db.String(100))  # e.g., "420", "Laser", "FJ"
    external_id = db.Column(db.String(200), unique=True)  # ID from theclubspot.com
    source_url = db.Column(db.String(500))  # URL on theclubspot.com
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    results = db.relationship('Result', back_populates='regatta', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Regatta {self.name}>'


class Result(db.Model):
    """Individual sailor results at regattas (ClubSpot data)"""
    __tablename__ = 'results'

    id = db.Column(db.Integer, primary_key=True)
    sailor_id = db.Column(db.Integer, db.ForeignKey('sailors.id'), nullable=False, index=True)
    regatta_id = db.Column(db.Integer, db.ForeignKey('regattas.id'), nullable=False, index=True)

    # Result details
    placement = db.Column(db.Integer, nullable=False)  # PRIMARY data point
    boat_type = db.Column(db.String(100))  # SECONDARY (e.g., "420", "Laser")
    role = db.Column(db.String(20))  # TERTIARY: 'skipper' or 'crew'
    points_scored = db.Column(db.Float)  # QUATERNARY
    division = db.Column(db.String(50))  # e.g., "A Division", "Varsity"

    # Additional metadata
    team_name = db.Column(db.String(200))  # School/club team
    crew_partner = db.Column(db.String(200))  # If skipper, who was crew (and vice versa)

    # RAW DATA - Complete pipe-separated row text from scraper
    raw_row_data = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    sailor = db.relationship('Sailor', back_populates='results')
    regatta = db.relationship('Regatta', back_populates='results')

    # Unique constraint: one sailor can't have multiple results for same regatta+division
    __table_args__ = (
        db.Index('idx_sailor_regatta', 'sailor_id', 'regatta_id'),
    )

    def __repr__(self):
        return f'<Result {self.sailor.name} - {self.regatta.name}: {self.placement}>'


class SailorName(db.Model):
    """All sailor names from HS/College scrapers with account claiming status"""
    __tablename__ = 'sailor_names'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    name_normalized = db.Column(db.String(200), unique=True, nullable=False, index=True)

    # Account claiming. As on Sailor, `is_claimed` is denormalized from
    # ProfileClaim; `user_id`/`claimed_at` predate it and are kept in sync for
    # back-compat (nothing read them before -- they were never written).
    is_claimed = db.Column(db.Boolean, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    claimed_at = db.Column(db.DateTime)

    # Metadata
    school = db.Column(db.String(200))  # School affiliation if available
    graduation_year = db.Column(db.Integer)
    source = db.Column(db.String(20))  # 'hs', 'college', or 'both'

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = db.relationship('User', backref='legacy_claimed_sailor_names',
                           foreign_keys=[user_id])
    claim = db.relationship('ProfileClaim', back_populates='sailor_name', uselist=False)
    hs_results = db.relationship('HSResult', back_populates='sailor_name', cascade='all, delete-orphan')
    college_results = db.relationship('CollegeResult', back_populates='sailor_name', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<SailorName {self.name}>'


class HSResult(db.Model):
    """High School sailing results"""
    __tablename__ = 'hs_results'

    id = db.Column(db.Integer, primary_key=True)
    sailor_name_id = db.Column(db.Integer, db.ForeignKey('sailor_names.id'), nullable=False, index=True)

    # Sailor info
    sailor_name = db.relationship('SailorName', back_populates='hs_results')

    # Regatta info
    regatta_name = db.Column(db.String(300), nullable=False, index=True)
    regatta_link = db.Column(db.String(500))
    regatta_date = db.Column(db.Date, index=True)

    # Result details
    place = db.Column(db.String(50))  # "21/32" format (place finished/total sailors)
    place_numeric = db.Column(db.Integer)  # Just the 21 for sorting
    total_boats = db.Column(db.Integer)  # Total boats in race
    position = db.Column(db.String(20))  # "Skipper" or "Crew"
    division = db.Column(db.String(50))  # "A Div", "B Div", etc.

    # School/Team
    school = db.Column(db.String(200))

    # Raw data
    raw_row_data = db.Column(db.Text)  # Pipe-separated original row

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Unique constraint
    __table_args__ = (
        db.Index('idx_hs_sailor_regatta', 'sailor_name_id', 'regatta_name'),
    )

    def __repr__(self):
        return f'<HSResult {self.sailor_name.name if self.sailor_name else "?"} - {self.regatta_name}>'


class CollegeResult(db.Model):
    """College sailing results"""
    __tablename__ = 'college_results'

    id = db.Column(db.Integer, primary_key=True)
    sailor_name_id = db.Column(db.Integer, db.ForeignKey('sailor_names.id'), nullable=False, index=True)

    # Sailor info
    sailor_name = db.relationship('SailorName', back_populates='college_results')

    # Regatta info
    regatta_name = db.Column(db.String(300), nullable=False, index=True)
    regatta_link = db.Column(db.String(500))
    regatta_date = db.Column(db.Date, index=True)

    # Result details
    place = db.Column(db.String(50))  # "21/32" format (place finished/total sailors)
    place_numeric = db.Column(db.Integer)  # Just the 21 for sorting
    total_boats = db.Column(db.Integer)  # Total boats in race
    position = db.Column(db.String(20))  # "Skipper" or "Crew"
    division = db.Column(db.String(50))  # "A Div", "B Div", etc.

    # School/Team
    school = db.Column(db.String(200))

    # Raw data
    raw_row_data = db.Column(db.Text)  # Pipe-separated original row

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Unique constraint
    __table_args__ = (
        db.Index('idx_college_sailor_regatta', 'sailor_name_id', 'regatta_name'),
    )

    def __repr__(self):
        return f'<CollegeResult {self.sailor_name.name if self.sailor_name else "?"} - {self.regatta_name}>'


class ResumeLink(db.Model):
    """Shareable resume links created by sailors"""
    __tablename__ = 'resume_links'

    id = db.Column(db.Integer, primary_key=True)

    # Owner. Was `sailor_id` (ClubSpot only), which locked HS/College sailors
    # out of resumes entirely; the owner is now the account.
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    sailor_id = db.Column(db.Integer, db.ForeignKey('sailors.id'), nullable=True, index=True)

    token = db.Column(db.String(32), unique=True, nullable=False, index=True)

    # Resume customization
    title = db.Column(db.String(200), default='Sailing Resume')
    custom_bio = db.Column(db.Text)
    # Qualified result keys, e.g. ["clubspot:1203", "hs:88471", "manual:14"].
    # Bare ints are legacy ClubSpot `Result.id`s and still resolve.
    selected_result_ids = db.Column(db.JSON)
    template_style = db.Column(db.String(50), default='modern')  # 'modern', 'classic', 'minimal'

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_accessed = db.Column(db.DateTime)
    access_count = db.Column(db.Integer, default=0)

    # Relationships
    user = db.relationship('User', backref='resume_links')
    sailor = db.relationship('Sailor', back_populates='resume_links')

    def __repr__(self):
        return f'<ResumeLink {self.token}>'

    @staticmethod
    def generate_token():
        """Generate unique token for shareable links"""
        return secrets.token_urlsafe(24)


class ScraperLog(db.Model):
    """Log of scraper runs"""
    __tablename__ = 'scraper_logs'

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    status = db.Column(db.String(20))  # 'running', 'completed', 'failed'
    regattas_scraped = db.Column(db.Integer, default=0)
    sailors_added = db.Column(db.Integer, default=0)
    results_added = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text)

    # Run accounting for the backlog drain (ClubSpot runs on GitHub Actions)
    regattas_listed = db.Column(db.Integer)     # what the API listed
    regattas_skipped = db.Column(db.Integer)    # already scraped or checked empty
    regattas_remaining = db.Column(db.Integer)  # left unscraped when the run stopped
    regattas_empty = db.Column(db.Integer)      # visited this run, no results posted
    duration_seconds = db.Column(db.Integer)

    def __repr__(self):
        return f'<ScraperLog {self.started_at} - {self.status}>'


class ClubspotRegattaCheck(db.Model):
    """
    The ClubSpot catalogue: every regatta ID ever seen, and every visit.

    Rows are added by the enumeration walk (source 'api') or the sitemap
    ('sitemap') with last_checked_at NULL, and updated when the scraper
    visits them. Regattas with results are also stored in `regattas`; empty
    ones live only here, so they are never re-visited forever.
    """
    __tablename__ = 'clubspot_regatta_checks'

    external_id = db.Column(db.String(32), primary_key=True)  # ClubSpot objectId
    name = db.Column(db.String(300))
    start_date = db.Column(db.Date, index=True)
    end_date = db.Column(db.Date)
    last_checked_at = db.Column(db.DateTime, index=True)  # NULL = never visited
    result_count = db.Column(db.Integer, nullable=False, default=0)
    check_count = db.Column(db.Integer, nullable=False, default=0)

    # Where and when the ID was seen (catalogue bookkeeping)
    first_seen_at = db.Column(db.DateTime)
    last_seen_at = db.Column(db.DateTime)
    source = db.Column(db.String(20))             # 'api' or 'sitemap'
    remote_created_at = db.Column(db.DateTime)    # ClubSpot's createdAt

    def __repr__(self):
        return f'<ClubspotRegattaCheck {self.external_id} results={self.result_count}>'


class ScraperState(db.Model):
    """Small key/value store for scraper markers (enumeration frontiers etc.)"""
    __tablename__ = 'scraper_state'

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<ScraperState {self.key}>'


class ScraperLogEntry(db.Model):
    """Per-line progress output from scraper runs, streamed to the admin UI"""
    __tablename__ = 'scraper_log_entries'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    level = db.Column(db.String(10))  # 'INFO', 'WARNING', 'ERROR'
    message = db.Column(db.Text, nullable=False)

    # Structured fields set only on section-completion rows; NULL on plain lines
    step = db.Column(db.String(20))     # 'schools', 'rosters', 'results'
    section = db.Column(db.String(20))  # 'scrape', 'store'
    season = db.Column(db.String(10))   # e.g. 'f25'; NULL for step 1
    source = db.Column(db.String(20))   # 'hs' or 'college'

    def __repr__(self):
        return f'<ScraperLogEntry {self.id} {self.level}>'


class School(db.Model):
    """Schools from HS/College scrapers"""
    __tablename__ = 'schools'

    id = db.Column(db.Integer, primary_key=True)

    # School identification
    name = db.Column(db.String(300), nullable=False, index=True)
    district = db.Column(db.String(200))  # Geographic district/conference
    source = db.Column(db.String(20), nullable=False)  # 'hs' or 'college'

    # URL information
    url_slug = db.Column(db.String(200), nullable=False)  # e.g., "southwestern-central"
    full_url = db.Column(db.String(500))  # Full URL to school page

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Unique constraint: same school can appear in both HS and College
    __table_args__ = (
        db.Index('idx_school_name_source', 'name', 'source'),
        db.UniqueConstraint('url_slug', 'source', name='uq_school_slug_source'),
    )

    def __repr__(self):
        return f'<School {self.name} ({self.source})>'
