from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import secrets

db = SQLAlchemy()

class User(UserMixin, db.Model):
    """User accounts (optional login for sailors/coaches)"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='sailor')  # 'sailor' or 'coach'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    # Relationship to sailor (if claiming a profile)
    sailor_id = db.Column(db.Integer, db.ForeignKey('sailors.id'), nullable=True)
    sailor = db.relationship('Sailor', back_populates='user', foreign_keys=[sailor_id])

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

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

    # Claimed profile
    is_claimed = db.Column(db.Boolean, default=False)
    user = db.relationship('User', back_populates='sailor', foreign_keys=[User.sailor_id])

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

    # Account claiming
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
    user = db.relationship('User', backref='claimed_sailor_names')
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
    sailor_id = db.Column(db.Integer, db.ForeignKey('sailors.id'), nullable=False, index=True)
    token = db.Column(db.String(32), unique=True, nullable=False, index=True)

    # Resume customization
    title = db.Column(db.String(200), default='Sailing Resume')
    custom_bio = db.Column(db.Text)
    selected_result_ids = db.Column(db.JSON)  # List of result IDs to include
    template_style = db.Column(db.String(50), default='modern')  # 'modern', 'classic', 'minimal'

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_accessed = db.Column(db.DateTime)
    access_count = db.Column(db.Integer, default=0)

    # Relationships
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

    def __repr__(self):
        return f'<ScraperLog {self.started_at} - {self.status}>'
