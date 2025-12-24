import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base configuration"""
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

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

    # APScheduler settings
    SCHEDULER_API_ENABLED = True
    SCHEDULER_TIMEZONE = 'UTC'

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
