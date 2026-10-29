"""
Test fixtures: the Flask app on a throw-away SQLite file, scraper scheduler
off, every test starting from empty tables.
"""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_db_dir = tempfile.mkdtemp(prefix='regatta-tests-')
os.environ['DATABASE_URL'] = f"sqlite:///{os.path.join(_db_dir, 'test.db')}"
os.environ['SCRAPER_ENABLED'] = 'false'
os.environ['FLASK_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'test-only-secret'
os.environ.setdefault('STATS_MIN_FLEET', '5')

from app import app as flask_app  # noqa: E402
from models import db as _db  # noqa: E402


@pytest.fixture(scope='session')
def app():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return flask_app


@pytest.fixture
def db(app):
    """Empty tables for every test, inside an app context"""
    with app.app_context():
        _db.drop_all()
        _db.create_all()
        yield _db
        _db.session.remove()


@pytest.fixture
def client(app, db):
    return app.test_client()


@pytest.fixture
def runner(app, db):
    return app.test_cli_runner()
