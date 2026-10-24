"""
Web scraper for theclubspot.com regatta results
Uses Parse API to fetch all regatta IDs, then Selenium to scrape results
"""
import requests
from datetime import datetime, timedelta, timezone
from models import db, Sailor, Regatta, Result, ScraperLog, ClubspotRegattaCheck
from scraper_logging import attach_db_log_handler
from clubspot_common import (HARVEST_JS, extract_placement, fetch_regatta_by_id,
                             parse_parse_date, parse_result_row)
import clubspot_catalog as catalog
import gc
import logging
import os
import time
from threading import Thread, Lock
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def make_driver():
    """
    Create a headless Chrome driver with a minimal memory footprint.
    The scraper shares a 512MB container with gunicorn, so Chrome runs as a
    single process with one renderer and a capped JS heap.
    """
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--disable-extensions")
    options.add_argument("--log-level=3")
    # Memory limiters: one renderer, no zygote forks, near-zero disk
    # cache, small JS heap, modest window. NOTE: --single-process is NOT
    # used -- it deadlocks modern headless Chrome during startup, which
    # froze whole runs with Selenium blocked forever on the handshake.
    options.add_argument("--no-zygote")
    options.add_argument("--renderer-process-limit=1")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disk-cache-size=1048576")
    options.add_argument("--js-flags=--max-old-space-size=128")
    options.add_argument("--window-size=1280,720")
    options.page_load_strategy = "eager"
    chrome_bin = _find_chrome_binary()
    if chrome_bin:
        options.binary_location = chrome_bin
    chromedriver = _find_chromedriver()
    logger.info(f"Chrome binary: {chrome_bin or 'system default'}; "
                f"chromedriver: {chromedriver or 'via Selenium Manager'}")
    service = ChromeService(executable_path=chromedriver) if chromedriver else None
    driver = _start_chrome_with_timeout(options, service=service, seconds=90)
    driver.set_page_load_timeout(30)
    driver.set_script_timeout(30)
    return driver


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_chrome_binary():
    """CHROME_BIN env first, then the copies build.sh installs under .chrome/"""
    candidates = [
        os.environ.get('CHROME_BIN'),
        os.path.join(_BASE_DIR, '.chrome', 'chrome-headless-shell-linux64', 'chrome-headless-shell'),
        os.path.join(_BASE_DIR, '.chrome', 'chrome-linux64', 'chrome'),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _find_chromedriver():
    """CHROMEDRIVER_PATH env first, then the copy build.sh installs"""
    candidates = [
        os.environ.get('CHROMEDRIVER_PATH'),
        os.path.join(_BASE_DIR, '.chrome', 'chromedriver-linux64', 'chromedriver'),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _start_chrome_with_timeout(options, service=None, seconds=90):
    """
    Start Chrome in a helper thread so a wedged startup handshake cannot
    block the scraper forever (Selenium has no client-side timeout).
    """
    holder = {}

    def target():
        try:
            driver = webdriver.Chrome(options=options, service=service)
            if holder.get('timed_out'):
                # Too late to be used; don't leak the browser
                try:
                    driver.quit()
                except Exception:
                    pass
            else:
                holder['driver'] = driver
        except BaseException as e:
            holder['error'] = e

    thread = Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        holder['timed_out'] = True
        raise WebDriverException(f"Chrome did not start within {seconds}s")
    if 'error' in holder:
        raise holder['error']
    return holder['driver']


def _safe_quit(driver):
    """quit() with a timeout; kill the chromedriver process if it hangs"""
    if driver is None:
        return

    def target():
        try:
            driver.quit()
        except Exception:
            pass

    thread = Thread(target=target, daemon=True)
    thread.start()
    thread.join(15)
    if thread.is_alive():
        try:
            driver.service.process.kill()
        except Exception:
            pass


def container_memory_mb():
    """
    (used_mb, limit_mb) for this container from cgroups; MEMORY_LIMIT_MB env
    overrides the limit. Either value is None when unavailable.
    """
    used = limit = None
    try:
        # cgroup v2
        with open('/sys/fs/cgroup/memory.current') as f:
            used = int(f.read()) / (1024 * 1024)
        with open('/sys/fs/cgroup/memory.max') as f:
            raw = f.read().strip()
            limit = None if raw == 'max' else int(raw) / (1024 * 1024)
    except (OSError, ValueError):
        try:
            # cgroup v1
            with open('/sys/fs/cgroup/memory/memory.usage_in_bytes') as f:
                used = int(f.read()) / (1024 * 1024)
            with open('/sys/fs/cgroup/memory/memory.limit_in_bytes') as f:
                raw_limit = int(f.read())
                # Huge values mean "no limit set"
                limit = None if raw_limit >= (1 << 60) else raw_limit / (1024 * 1024)
        except (OSError, ValueError):
            pass

    env_limit = os.environ.get('MEMORY_LIMIT_MB')
    if env_limit:
        try:
            limit = float(env_limit)
        except ValueError:
            pass

    return used, limit


class ClubspotScraper:
    """Scraper for theclubspot.com regatta results"""

    # Watchdog thresholds as fractions of the container memory limit:
    # above SOFT restart Chrome to reclaim memory, above HARD stop the run
    # cleanly before the kernel OOM-kills the whole web service
    MEMORY_SOFT_FRACTION = 0.78
    MEMORY_HARD_FRACTION = 0.88

    # Chrome accumulates memory across page loads; restart it periodically
    RESTART_DRIVER_EVERY = 20

    # An empty regatta is re-checked at most this often while it is still
    # inside the re-check window (SCRAPER_RECHECK_EMPTY_DAYS after it ended)
    RECHECK_EMPTY_INTERVAL_DAYS = 2

    def _memory_state(self):
        """('ok'|'high'|'critical', used_mb, limit_mb); 'ok' when unknown"""
        used, limit = container_memory_mb()
        if used is None or limit is None or limit <= 0:
            return 'ok', used, limit
        fraction = used / limit
        if fraction >= self.MEMORY_HARD_FRACTION:
            return 'critical', used, limit
        if fraction >= self.MEMORY_SOFT_FRACTION:
            return 'high', used, limit
        return 'ok', used, limit

    def _restart_driver(self, driver):
        _safe_quit(driver)
        gc.collect()
        return make_driver()

    def __init__(self, log_id=None):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.stats = {
            'regattas_scraped': 0,
            'regattas_empty': 0,
            'sailors_added': 0,
            'results_added': 0
        }
        self.log_id = log_id  # Track which ScraperLog we're updating

    def should_stop(self):
        """Check if we should stop scraping (user requested cancellation)"""
        if not self.log_id:
            return False

        try:
            # The cancel flag is set by another process (the admin Stop
            # button in the web service), so the cached row in this
            # session's identity map would never show it.
            db.session.expire_all()
            log = ScraperLog.query.get(self.log_id)
            if log and log.status == 'cancelled':
                logger.info("Stop requested by user, cancelling scraper...")
                return True
        except Exception as e:
            logger.error(f"Error checking stop status: {e}")

        return False

    def _write_run_counters(self, log, total_listed, skipped, remaining, run_started):
        """Run accounting the job summary and admin panel report"""
        log.regattas_scraped = self.stats['regattas_scraped']
        log.sailors_added = self.stats['sailors_added']
        log.results_added = self.stats['results_added']
        log.regattas_listed = total_listed
        log.regattas_skipped = skipped
        log.regattas_remaining = remaining
        log.regattas_empty = self.stats['regattas_empty']
        log.duration_seconds = int(time.monotonic() - run_started)

    def fetch_all_regatta_ids(self, limit=None, start_year=None):
        """
        Kept for callers that still expect a list: the catalogue rows that
        are due, as API-shaped dicts. Enumeration itself lives in
        clubspot_catalog.update_catalog.
        """
        due, _ = catalog.select_due(start_year=start_year, limit=limit)
        return [{'objectId': c.external_id, 'name': c.name} for c in due]

    def scrape_all_regattas(self, limit=None, start_year=None):
        """
        Main entry point: update the regatta catalogue (date-window walk,
        see clubspot_catalog), then scrape every catalogued regatta that is
        due -- finished, never visited (or empty and worth re-checking) --
        newest first.

        Args:
            limit: Maximum number of regattas to visit this run
            start_year: Only scrape regattas from this year onwards
                        (default: no floor -- everything in the catalogue)
        """
        # A crashed or hung run leaves its row 'running' forever; mark such
        # rows failed so they can't confuse stop requests or the admin UI
        stale = ScraperLog.query.filter_by(status='running').update(
            {'status': 'failed', 'error_message': 'Stale run superseded by a newer run'},
            synchronize_session=False
        )
        log = ScraperLog(status='running')
        db.session.add(log)
        db.session.commit()
        self.log_id = log.id

        attach_db_log_handler(logger)
        if stale:
            logger.warning(f"Marked {stale} stale 'running' scraper log(s) as failed")

        try:
            logger.info("Starting scraper...")
            run_started = time.monotonic()

            # Phase 1: bring the catalogue up to date -- a date-window walk
            # that never uses skip (clubspot_catalog). A failure here is not
            # fatal: the catalogue built by earlier runs is still there.
            try:
                catalog.update_catalog(logger.info, now=datetime.now(timezone.utc))
            except Exception as e:
                logger.error(f"Catalogue update failed: {e}; scraping from the existing catalogue")

            synced = catalog.sync_results_into_catalog()
            if synced:
                logger.info(f"Marked {synced} catalogue rows as scraped from results already stored")

            # Phase 2: what is due. Regattas with results are skipped; empties
            # are re-checked only inside the re-check window after they ended
            # and not more often than the interval, so a run never re-visits
            # every empty regatta in ClubSpot's history.
            recheck_window_days = int(os.environ.get('SCRAPER_RECHECK_EMPTY_DAYS', '30'))
            regattas_data, catalog_counts = catalog.select_due(
                recheck_window_days=recheck_window_days,
                recheck_interval_days=self.RECHECK_EMPTY_INTERVAL_DAYS,
                start_year=start_year, limit=limit)
            total_listed = catalog_counts['catalog']
            skipped = total_listed - len(regattas_data)
            logger.info(f"Catalogue: {total_listed} regattas, {catalog_counts['with_results']} "
                        f"with results stored, {len(regattas_data)} due to scrape now")

            if not regattas_data:
                logger.info("Nothing due to scrape")
                log.status = 'completed'
                log.completed_at = datetime.utcnow()
                self._write_run_counters(log, total_listed, skipped, 0, run_started)
                db.session.commit()
                return self.stats

            used_mb, limit_mb = container_memory_mb()
            if used_mb is not None and limit_mb:
                logger.info(f"Memory at start: {used_mb:.0f}MB used of {limit_mb:.0f}MB limit")

            # Bound each run by wall-clock time, not a regatta count: on
            # GitHub Actions the real limit is the job timeout, and stopping
            # cleanly a little before it keeps the run log and job summary
            # intact. Whatever is left is picked up by the next run. 0 = no
            # budget.
            budget_min = float(os.environ.get('SCRAPER_TIME_BUDGET_MIN', '300'))
            if budget_min:
                logger.info(f"Time budget for this run: {budget_min:.0f} minutes "
                            f"(enumeration already used {(time.monotonic() - run_started) / 60:.0f})")

            # Fail fast if Chrome can't start (e.g. not installed on this
            # host): every regatta needs it, and swallowing the error
            # per-regatta would burn hours writing regattas with no results.
            logger.info("Starting headless Chrome...")
            try:
                driver = make_driver()
            except WebDriverException as e:
                raise RuntimeError(
                    f"Could not start Chrome for results scraping "
                    f"(is Chrome installed on this host?): {e}"
                ) from e
            logger.info("Chrome started")

            scraped_since_restart = 0
            stopped_early = None
            remaining = 0
            try:
                for idx, regatta_data in enumerate(regattas_data, 1):
                    # Check if user requested stop
                    if self.should_stop():
                        log.status = 'cancelled'
                        log.completed_at = datetime.utcnow()
                        self._write_run_counters(log, total_listed, skipped,
                                                 len(regattas_data) - idx + 1, run_started)
                        db.session.commit()
                        logger.info(f"Scraper cancelled by user after {idx-1} regattas")
                        return self.stats

                    regatta_id = regatta_data.external_id
                    regatta_name = regatta_data.name or 'Unknown'

                    elapsed_min = (time.monotonic() - run_started) / 60
                    if budget_min and elapsed_min >= budget_min:
                        remaining = len(regattas_data) - idx + 1
                        stopped_early = (
                            f"Reached the time budget of {budget_min:.0f} minutes "
                            f"after {self.stats['regattas_scraped']} regattas "
                            f"({remaining} still unscraped)"
                        )
                        break

                    # Memory watchdog: restart Chrome above the soft
                    # threshold, stop the run above the hard threshold --
                    # never let the container hit its cap and get OOM-killed.
                    # The >=3 guard avoids restart thrash when a freshly
                    # started Chrome already sits near the threshold.
                    state, used_mb, limit_mb = self._memory_state()
                    if state != 'ok' and scraped_since_restart >= 3:
                        logger.warning(
                            f"Memory at {used_mb:.0f}MB of {limit_mb:.0f}MB -- "
                            f"restarting Chrome to reclaim memory"
                        )
                        driver = self._restart_driver(driver)
                        scraped_since_restart = 0
                        state, used_mb, limit_mb = self._memory_state()
                    if state == 'critical':
                        remaining = len(regattas_data) - idx + 1
                        stopped_early = (
                            f"Stopped to stay under the memory cap "
                            f"({used_mb:.0f}MB used of {limit_mb:.0f}MB "
                            f"with a fresh Chrome)"
                        )
                        break

                    logger.info(f"[{idx}/{len(regattas_data)}] Scraping: {regatta_name} ({regatta_id})")

                    try:
                        self._scrape_regatta(regatta_data, driver)
                        self.stats['regattas_scraped'] += 1
                        scraped_since_restart += 1

                        if scraped_since_restart >= self.RESTART_DRIVER_EVERY:
                            logger.info(
                                f"Restarting Chrome after {scraped_since_restart} "
                                f"regattas to keep memory flat"
                            )
                            driver = self._restart_driver(driver)
                            scraped_since_restart = 0

                        # Update progress in database periodically
                        if idx % 10 == 0:
                            log.regattas_scraped = self.stats['regattas_scraped']
                            log.sailors_added = self.stats['sailors_added']
                            log.results_added = self.stats['results_added']
                            db.session.commit()

                        time.sleep(2)  # Be polite, don't hammer the server
                    except WebDriverException as e:
                        logger.warning(f"Chrome session lost while scraping {regatta_id}: {e}; restarting browser")
                        driver = self._restart_driver(driver)  # if this raises, the run fails loudly
                        scraped_since_restart = 0
                    except Exception as e:
                        logger.error(f"Error scraping {regatta_id}: {e}")
                        continue
            finally:
                _safe_quit(driver)
                gc.collect()

            log.status = 'completed'
            log.completed_at = datetime.utcnow()
            self._write_run_counters(log, total_listed, skipped, remaining, run_started)
            if stopped_early:
                log.error_message = f"{stopped_early}; remaining regattas will be picked up on the next run"
                logger.info(f"Run stopped early: {stopped_early}")
            db.session.commit()

            logger.info(
                f"Scraping complete in {log.duration_seconds // 60} min: {total_listed} listed, "
                f"{skipped} skipped, {self.stats['regattas_scraped']} visited "
                f"({self.stats['regattas_empty']} with no results posted), "
                f"{self.stats['results_added']} results added, {remaining} still unscraped"
            )
            return self.stats

        except Exception as e:
            log.status = 'failed'
            log.error_message = str(e)
            log.completed_at = datetime.utcnow()
            db.session.commit()
            logger.error(f"Scraper failed: {e}")
            raise

    def _scrape_regatta(self, check, driver):
        """
        Scrape one catalogued regatta's results page with Selenium and store
        what it finds.

        Args:
            check: the ClubspotRegattaCheck row (catalogue entry)
            driver: Shared Selenium driver (reused across regattas)
        """
        regatta_id = check.external_id
        url = f"https://theclubspot.com/regatta/{regatta_id}"

        try:
            # Results first: a regatta row is only worth storing once it has
            # results. Creating the row up front left every regatta a run
            # ever looked at -- including ones with nothing posted --
            # permanently in the table.
            results_url = f"{url}/results?list_view=true"
            results_data = self._scrape_results_with_selenium(results_url, regatta_id, driver)

            # Remember the visit either way, so empties are not re-visited
            # on every future run
            self._record_check(check, len(results_data))

            if not results_data:
                self.stats['regattas_empty'] += 1
                logger.info(f"No results posted for {regatta_id}; not storing a regatta row")
                return

            regatta = self._get_or_create_regatta(self._regatta_metadata(check, url))

            for result_data in results_data:
                self._save_result(result_data, regatta.id)

        except Exception as e:
            logger.error(f"Error in _scrape_regatta for {regatta_id}: {e}")
            raise

    def _regatta_metadata(self, check, url):
        """
        Regatta row fields from the catalogue entry. The listing carries no
        club (dropping `include` kept it small), so the club name -- and the
        dates, for sitemap-only entries that have none -- come from one
        per-regatta lookup, made only when a regatta is actually stored.
        """
        data = {
            'source_url': url,
            'external_id': check.external_id,
            'name': check.name or f'Regatta {check.external_id}',
            'start_date': check.start_date,
            'end_date': check.end_date,
        }
        try:
            remote = fetch_regatta_by_id(check.external_id, include_club=True)
        except Exception as e:
            logger.warning(f"Could not fetch details for regatta {check.external_id}: {e}")
            remote = None
        if remote:
            start = parse_parse_date(remote.get('startDate'))
            end = parse_parse_date(remote.get('endDate'))
            data['name'] = remote.get('name') or data['name']
            data['start_date'] = start.date() if start else data['start_date']
            data['end_date'] = end.date() if end else data['end_date']
            club = remote.get('clubObject')
            if isinstance(club, dict) and club.get('name'):
                data['location'] = club['name']
            # Keep the catalogue row current as well
            check.name = data['name']
            check.start_date = data['start_date']
            check.end_date = data['end_date']
        if data['start_date'] is None:
            data['start_date'] = datetime.utcnow().date()
        return data

    def _scrape_results_with_selenium(self, results_url, regatta_id, driver, timeout=12):
        """
        Scrape results page using Selenium to handle JavaScript rendering
        Based on user's proven scraping logic

        Args:
            results_url: URL of the results page
            regatta_id: Database ID of the regatta
            driver: Shared Selenium driver (owned by the caller; WebDriver
                    errors propagate so the caller can restart the browser)
            timeout: Seconds to wait for page to load

        Returns:
            List of result dicts with sailor names and placements
        """
        results = []

        driver.get(results_url)

        # Wait for any table rows to appear
        def any_rows_present(d):
            # Classic table rows with TDs
            if d.find_elements(By.CSS_SELECTOR, "table tbody tr td"):
                return True
            # Virtualized/data-grid rows
            if d.find_elements(By.CSS_SELECTOR, "[role='row'] [role='gridcell'], .ag-row .ag-cell"):
                return True
            return False

        try:
            WebDriverWait(driver, timeout).until(any_rows_present)
        except TimeoutException:
            logger.warning(f"Timeout waiting for results table at {results_url}")
            return []

        # Scroll to load lazy content
        for _ in range(8):
            driver.execute_script("window.scrollBy(0, Math.max(600, window.innerHeight));")
            time.sleep(0.2)

        # Extract all row data
        rows_text = driver.execute_script(HARVEST_JS) or []

        # Parse each row to extract sailor name and placement
        for row_text in rows_text:
            result_data = self._parse_result_row(row_text)
            if result_data:
                results.append(result_data)

        logger.info(f"Extracted {len(results)} results from {results_url}")
        return results

    def _parse_result_row(self, row_text):
        """
        Parse a single row of results text to extract sailor data

        Args:
            row_text: Pipe-separated row text (e.g., "1 | 12345 | John Doe | 15.0")

        Returns:
            Dict with sailor_name, placement, and optionally points_scored
        """
        try:
            return parse_result_row(row_text)
        except Exception as e:
            logger.debug(f"Error parsing result row: {e}")
            return None

    def _save_result(self, result_data, regatta_id):
        """Save a single result to the database"""
        sailor_name = result_data.get('sailor_name')
        if not sailor_name:
            return

        # Get or create sailor
        sailor = self._get_or_create_sailor(sailor_name)

        # Check if result already exists
        existing = Result.query.filter_by(
            sailor_id=sailor.id,
            regatta_id=regatta_id
        ).first()

        if existing:
            logger.debug(f"Result already exists: {sailor_name} at regatta {regatta_id}")
            return

        # Create new result
        result = Result(
            sailor_id=sailor.id,
            regatta_id=regatta_id,
            placement=result_data['placement'],
            boat_type=result_data.get('boat_type'),
            role=result_data.get('role'),
            points_scored=result_data.get('points_scored'),
            division=result_data.get('division'),
            team_name=result_data.get('team_name'),
            raw_row_data=result_data.get('raw_row_data')
        )

        db.session.add(result)
        db.session.commit()
        self.stats['results_added'] += 1
        logger.debug(f"Added result: {sailor_name} - {result_data['placement']}")

    def _get_or_create_sailor(self, name):
        """Get existing sailor or create new one"""
        name_normalized = name.lower().strip()

        sailor = Sailor.query.filter_by(name_normalized=name_normalized).first()

        if not sailor:
            sailor = Sailor(
                name=name.strip(),
                name_normalized=name_normalized
            )
            db.session.add(sailor)
            db.session.commit()
            self.stats['sailors_added'] += 1
            logger.debug(f"Added new sailor: {name}")

        return sailor

    def _get_or_create_regatta(self, data):
        """Get existing regatta or create new one"""
        external_id = data.get('external_id')

        regatta = Regatta.query.filter_by(external_id=external_id).first()

        if not regatta:
            regatta = Regatta(
                name=data.get('name', 'Unknown Regatta'),
                location=data.get('location'),
                start_date=data.get('start_date', datetime.utcnow().date()),
                end_date=data.get('end_date'),
                fleet_type=data.get('fleet_type'),
                external_id=external_id,
                source_url=data.get('source_url')
            )
            db.session.add(regatta)
            db.session.commit()
            logger.debug(f"Added new regatta: {data.get('name')}")

        return regatta

    def _record_check(self, check, result_count):
        """Update the catalogue row for a visit; never lets a failure stop the run"""
        try:
            check.last_checked_at = datetime.utcnow()
            check.result_count = result_count
            check.check_count = (check.check_count or 0) + 1
            db.session.add(check)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning(f"Could not record the check for {check.external_id}: {e}")

    @staticmethod
    def _extract_placement(text):
        """Extract numeric placement from text"""
        return extract_placement(text)


# One scraper at a time: a second concurrent run would double Chrome's
# memory footprint and fight over the same regattas
_run_lock = Lock()


def run_scraper(limit=None, start_year=None):
    """
    Convenience function to run the scraper

    Args:
        limit: Max regattas to fetch from the API (default: all available)
        start_year: Only scrape regattas from this year onwards
                    (default: no floor -- everything ClubSpot has)
    """
    if not _run_lock.acquire(blocking=False):
        logger.warning("Scraper is already running in this process; not starting another")
        return {'skipped': 'already running'}
    try:
        scraper = ClubspotScraper()
        return scraper.scrape_all_regattas(limit=limit, start_year=start_year)
    finally:
        _run_lock.release()


def stop_scraper():
    """
    Stop any currently running scraper by marking it as cancelled

    Returns:
        True if a scraper was stopped, False otherwise
    """
    try:
        running_log = ScraperLog.query.filter_by(status='running').first()
        if running_log:
            running_log.status = 'cancelled'
            db.session.commit()
            logger.info(f"Marked scraper log {running_log.id} as cancelled")
            return True
        return False
    except Exception as e:
        logger.error(f"Error stopping scraper: {e}")
        return False
