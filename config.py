import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base configuration"""
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'

    # Fix for Render's postgres:// URLs - SQLAlchemy needs postgresql://
    database_url = os.environ.get('DATABASE_URL')
    if database_url and database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = database_url or 'sqlite:///app.db'  # Fallback for local dev

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Neon closes idle connections server-side; without pre_ping/recycle,
    # a pooled connection that's gone stale raises SSL errors into requests.
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 280,
    }

    # App settings
    APP_NAME = os.environ.get('APP_NAME', 'RegattaResume')
    BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')

    # Session configuration
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_SECURE = False  # Set to True in production with HTTPS
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Scraper settings
    SCRAPER_ENABLED = os.environ.get('SCRAPER_ENABLED', 'true').lower() == 'true'
    SCRAPER_USER_AGENT = os.environ.get('SCRAPER_USER_AGENT',
                                       'Mozilla/5.0 (compatible; RegattaResume/1.0)')
    THECLUBSPOT_BASE_URL = 'https://www.theclubspot.com'

    # Feature flags
    ENABLE_REGISTRATION = os.environ.get('ENABLE_REGISTRATION', 'true').lower() == 'true'
    ENABLE_PDF_EXPORT = os.environ.get('ENABLE_PDF_EXPORT', 'true').lower() == 'true'

    # Email (profile-claim verification). Off by default so local dev needs no
    # SMTP server: with ENABLE_EMAIL false, verification links are written to
    # the app log instead of sent.
    ENABLE_EMAIL = os.environ.get('ENABLE_EMAIL', 'false').lower() == 'true'
    SMTP_HOST = os.environ.get('SMTP_HOST')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SMTP_USERNAME = os.environ.get('SMTP_USERNAME')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')
    SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'true').lower() == 'true'
    MAIL_FROM = os.environ.get('MAIL_FROM', 'noreply@regattaresume.com')

    # Require a verified email before a resume link can be created. The claim
    # itself stays immediate -- this gates only the public artifact.
    REQUIRE_VERIFIED_EMAIL_TO_PUBLISH = os.environ.get(
        'REQUIRE_VERIFIED_EMAIL_TO_PUBLISH', 'true').lower() == 'true'
    EMAIL_VERIFY_TOKEN_MAX_AGE_HOURS = int(
        os.environ.get('EMAIL_VERIFY_TOKEN_MAX_AGE_HOURS', 48))

    # APScheduler settings
    SCHEDULER_API_ENABLED = True
    SCHEDULER_TIMEZONE = 'UTC'

    # GitHub Actions: the ClubSpot scraper runs there, not in this container
    # (it needs Chrome and more memory than the web service has). The admin
    # "Run ClubSpot Scraper" button dispatches that workflow.
    GITHUB_REPO = os.environ.get('GITHUB_REPO', 'jacktheone0/regatta_resume')
    GITHUB_WORKFLOW_FILE = os.environ.get('GITHUB_WORKFLOW_FILE', 'scrape.yml')
    GITHUB_WORKFLOW_REF = os.environ.get('GITHUB_WORKFLOW_REF', 'main')
    GITHUB_WORKFLOW_TOKEN = os.environ.get('GITHUB_WORKFLOW_TOKEN')

class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    SQLALCHEMY_ECHO = False

class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    SESSION_COOKIE_SECURE = True

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
