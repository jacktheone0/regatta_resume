"""
Shared scraper logging: mirrors scraper progress into scraper_log_entries
so the admin panel's live log (SSE) can stream it.
"""
import logging
from datetime import datetime

from sqlalchemy import text

from models import db


def db_log(message, level='INFO', step=None, section=None, season=None, source=None):
    """
    Insert one scraper_log_entries row on a pooled engine connection.
    Deliberately independent of db.session, which the scrapers commit in
    batches mid-run and must not be flushed or rolled back here.
    """
    try:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scraper_log_entries "
                    "(created_at, level, message, step, section, season, source) "
                    "VALUES (:created_at, :level, :message, :step, :section, :season, :source)"
                ),
                {
                    'created_at': datetime.utcnow(),
                    'level': level,
                    'message': message,
                    'step': step,
                    'section': section,
                    'season': season,
                    'source': source,
                }
            )
    except Exception:
        # Never let log persistence break a scrape, and never re-enter the
        # logger from here (the DB handler below would recurse).
        pass


class DBLogHandler(logging.Handler):
    """Mirrors a logger's records into scraper_log_entries."""

    def emit(self, record):
        db_log(record.getMessage(), level=record.levelname)


def attach_db_log_handler(logger):
    """Attach the DB handler once per logger; call from inside an app context."""
    if not any(isinstance(h, DBLogHandler) for h in logger.handlers):
        logger.addHandler(DBLogHandler())
