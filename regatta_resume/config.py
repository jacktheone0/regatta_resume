"""
Configuration management for Regatta Resume Builder.
Supports environment variables with sensible defaults.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """Base configuration"""
    # Flask settings
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')
    DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() in ('true', '1', 't')

    # Application settings
    BASE_DIR = Path(__file__).resolve().parent
    STATIC_FOLDER = BASE_DIR / "static"
    TEMPLATE_FOLDER = BASE_DIR / "templates"

    # File paths
    TEAMS_CSV = BASE_DIR / "teams.csv"
    SCRAPER_SCRIPT = BASE_DIR / "Resume.py"
    RESULTS_CSV = BASE_DIR / "results.csv"
    SCRAPER_DF_PATH = BASE_DIR / "scraper_df.csv"
    PDF_PATH = STATIC_FOLDER / "resume.pdf"
    RESUMES_DIR = STATIC_FOLDER / "resumes"

    # Scraper settings
    MAX_REGATTAS = int(os.getenv('MAX_REGATTAS', '250'))
    SCRAPER_TIMEOUT = int(os.getenv('SCRAPER_TIMEOUT', '12'))
    PAGE_LOAD_TIMEOUT = int(os.getenv('PAGE_LOAD_TIMEOUT', '30'))

    # Clubspot API settings
    CLUBSPOT_API_URL = os.getenv(
        'CLUBSPOT_API_URL',
        'https://theclubspot.com/parse/classes/regattas'
    )
    CLUBSPOT_API_LIMIT = int(os.getenv('CLUBSPOT_API_LIMIT', '15000'))

    # Techscore settings
    TECHSCORE_HS_URL = os.getenv(
        'TECHSCORE_HS_URL',
        'https://scores.hssailing.org/sailors/'
    )
    TECHSCORE_COLLEGE_URL = os.getenv(
        'TECHSCORE_COLLEGE_URL',
        'https://scores.collegesailing.org/sailors/'
    )

    # Selenium settings
    SELENIUM_HEADLESS = os.getenv('SELENIUM_HEADLESS', 'True').lower() in ('true', '1', 't')
    SELENIUM_DISABLE_GPU = os.getenv('SELENIUM_DISABLE_GPU', 'True').lower() in ('true', '1', 't')
    SELENIUM_NO_SANDBOX = os.getenv('SELENIUM_NO_SANDBOX', 'True').lower() in ('true', '1', 't')

    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = BASE_DIR / os.getenv('LOG_FILE', 'regatta_resume.log')

    # Security
    MAX_CONTENT_LENGTH = int(os.getenv('MAX_CONTENT_LENGTH', str(16 * 1024 * 1024)))  # 16MB
    SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'False').lower() in ('true', '1', 't')
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    @classmethod
    def init_app(cls):
        """Initialize application directories"""
        cls.STATIC_FOLDER.mkdir(parents=True, exist_ok=True)
        cls.RESUMES_DIR.mkdir(parents=True, exist_ok=True)


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True

    # Override with strong secret key in production
    SECRET_KEY = os.getenv('SECRET_KEY')
    if not SECRET_KEY or SECRET_KEY == 'dev-secret-key-change-in-production':
        raise ValueError("Must set SECRET_KEY environment variable in production")


class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    DEBUG = True
    # Use in-memory or temporary files for testing
    WTF_CSRF_ENABLED = False


# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}


def get_config(env=None):
    """Get configuration based on environment"""
    if env is None:
        env = os.getenv('FLASK_ENV', 'development')
    return config.get(env, config['default'])
