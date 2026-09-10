"""
Web scraper for theclubspot.com regatta results
Uses Parse API to fetch all regatta IDs, then Selenium to scrape results
"""
import requests
from datetime import datetime, timezone
from models import db, Sailor, Regatta, Result, ScraperLog
from scraper_logging import attach_db_log_handler
import gc
import logging
import os
import re
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
            'sailors_added': 0,
            'results_added': 0
        }
        self.log_id = log_id  # Track which ScraperLog we're updating

        # Parse API configuration for fetching regatta IDs
        self.parse_headers = {
            'Content-Type': 'text/plain',
            'Origin': 'https://theclubspot.com',
            'Referer': 'https://theclubspot.com/events',
            'User-Agent': 'Mozilla/5.0',
        }
        self.parse_api_url = 'https://theclubspot.com/parse/classes/regattas'

    def should_stop(self):
        """Check if we should stop scraping (user requested cancellation)"""
        if not self.log_id:
            return False

        try:
            log = ScraperLog.query.get(self.log_id)
            if log and log.status == 'cancelled':
                logger.info("Stop requested by user, cancelling scraper...")
                return True
        except Exception as e:
            logger.error(f"Error checking stop status: {e}")

        return False

    def fetch_all_regatta_ids(self, limit=None, start_year=None):
        """
        Fetch all regatta IDs from the Parse API

        Args:
            limit: Maximum number of regatta IDs to fetch (default: all)
            start_year: Only fetch regattas from this year onwards (e.g., 2024)

        Returns:
            List of dicts with regatta metadata: objectId, name, startDate, clubObject
        """
        logger.info("Fetching regatta IDs from Parse API...")

        # Build the where clause with date filter if provided
        where_clause = {
            'archived': {'$ne': True},
            'public': True,
            'clubObject': {'$nin': ['HCyTbbCF4n', 'XVgOrNASDY', 'ecNpKgrusD', 'GTKaJKeque', 'TTBnsppUug', 'pnBFlwJ2Mf']},
        }

        # Only regattas that have already started: newest-first ordering
        # otherwise fills each run with UPCOMING events that have no
        # results yet (and, never gaining results, they would eat the
        # per-run cap again every week)
        now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        start_filter = {'$lte': {'__type': 'Date', 'iso': now_iso}}
        if start_year:
            start_filter['$gte'] = {'__type': 'Date', 'iso': f"{start_year}-01-01T00:00:00.000Z"}
        where_clause['startDate'] = start_filter

        data = {
            'where': where_clause,
            'include': 'clubObject',
            'keys': 'objectId,name,startDate,endDate,clubObject.id,clubObject.name',
            'count': 1,
            'limit': limit or 15000,  # Fetch up to 15k regattas
            'order': '-startDate',
            '_method': 'GET',
            '_ApplicationId': 'myclubspot2017',
            '_ClientVersion': 'js4.3.1-forked-1.0',
            '_InstallationId': 'ce500aaa-c2a0-4d06-a9e3-1a558a606542',
        }

        try:
            response = requests.post(
                self.parse_api_url,
                headers=self.parse_headers,
                json=data,
                timeout=60
            )
            response.raise_for_status()
            payload = response.json()

            total_count = payload.get('count', 0)
            results = payload.get('results', [])

            logger.info(f"Parse API returned {len(results)} regattas (total available: {total_count})")
            return results

        except Exception as e:
            logger.error(f"Failed to fetch regatta IDs from Parse API: {e}")
            return []

    def scrape_all_regattas(self, limit=None, start_year=2024):
        """
        Main entry point: scrape all available regattas

        Args:
            limit: Maximum number of regattas to scrape
            start_year: Only scrape regattas from this year onwards (default: 2024)
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

            # Fetch regatta IDs from Parse API
            regattas_data = self.fetch_all_regatta_ids(limit=limit, start_year=start_year)

            if not regattas_data:
                logger.warning("No regattas found to scrape!")
                log.status = 'completed'
                log.completed_at = datetime.utcnow()
                db.session.commit()
                return self.stats

            # Regattas that already have stored results don't need re-scraping
            already_scraped = {
                ext_id for (ext_id,) in db.session.query(Regatta.external_id)
                .join(Result, Result.regatta_id == Regatta.id)
                .filter(Regatta.external_id.isnot(None))
                .distinct()
            }
            # Filter upfront instead of skipping inside the loop: the loop
            # previously ran a stop-check DB query per skipped regatta,
            # thousands of silent queries before any visible progress
            total_listed = len(regattas_data)
            regattas_data = [r for r in regattas_data
                             if r.get('objectId') not in already_scraped]
            skipped = total_listed - len(regattas_data)
            logger.info(f"{skipped} of {total_listed} regattas already have results; "
                        f"{len(regattas_data)} left to scrape")

            used_mb, limit_mb = container_memory_mb()
            if used_mb is not None and limit_mb:
                logger.info(f"Memory at start: {used_mb:.0f}MB used of {limit_mb:.0f}MB limit")

            # Bound each run; skipped-regatta logic makes the next run
            # continue where this one stopped
            max_per_run = int(os.environ.get('SCRAPER_MAX_REGATTAS_PER_RUN', '300'))

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
            try:
                for idx, regatta_data in enumerate(regattas_data, 1):
                    # Check if user requested stop
                    if self.should_stop():
                        log.status = 'cancelled'
                        log.completed_at = datetime.utcnow()
                        log.regattas_scraped = self.stats['regattas_scraped']
                        log.sailors_added = self.stats['sailors_added']
                        log.results_added = self.stats['results_added']
                        db.session.commit()
                        logger.info(f"Scraper cancelled by user after {idx-1} regattas")
                        return self.stats

                    regatta_id = regatta_data.get('objectId')
                    regatta_name = regatta_data.get('name', 'Unknown')

                    if max_per_run and self.stats['regattas_scraped'] >= max_per_run:
                        stopped_early = f"Reached the per-run cap of {max_per_run} regattas"
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
                        stopped_early = (
                            f"Stopped to stay under the memory cap "
                            f"({used_mb:.0f}MB used of {limit_mb:.0f}MB "
                            f"with a fresh Chrome)"
                        )
                        break

                    logger.info(f"[{idx}/{len(regattas_data)}] Scraping: {regatta_name} ({regatta_id})")

                    try:
                        self._scrape_regatta(regatta_id, regatta_data, driver)
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
            log.regattas_scraped = self.stats['regattas_scraped']
            log.sailors_added = self.stats['sailors_added']
            log.results_added = self.stats['results_added']
            if stopped_early:
                log.error_message = f"{stopped_early}; remaining regattas will be picked up on the next run"
                logger.info(f"Run stopped early: {stopped_early}")
            db.session.commit()

            logger.info(f"Scraping complete! Skipped {skipped} already-scraped regattas. Stats: {self.stats}")
            return self.stats

        except Exception as e:
            log.status = 'failed'
            log.error_message = str(e)
            log.completed_at = datetime.utcnow()
            db.session.commit()
            logger.error(f"Scraper failed: {e}")
            raise

    def _scrape_regatta(self, regatta_id, api_data=None, driver=None):
        """
        Scrape a single regatta by ID using Selenium

        Args:
            regatta_id: The clubspot regatta ID
            api_data: Optional dict with regatta metadata from Parse API
            driver: Shared Selenium driver (reused across regattas)
        """
        url = f"https://theclubspot.com/regatta/{regatta_id}"

        try:
            # Create or update regatta record using API data
            if api_data:
                regatta_metadata = self._parse_api_regatta_data(api_data, regatta_id, url)
            else:
                regatta_metadata = {
                    'source_url': url,
                    'external_id': regatta_id,
                    'name': f'Regatta {regatta_id}',
                    'start_date': datetime.utcnow().date()
                }

            regatta = self._get_or_create_regatta(regatta_metadata)

            # Now scrape results using Selenium
            results_url = f"{url}/results?list_view=true"
            results_data = self._scrape_results_with_selenium(results_url, regatta.id, driver)

            # Save results to database
            for result_data in results_data:
                self._save_result(result_data, regatta.id)

        except Exception as e:
            logger.error(f"Error in _scrape_regatta for {regatta_id}: {e}")
            raise

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

        # JavaScript to extract all data rows (excluding headers)
        HARVEST_JS = r"""
const out = new Set();

// 1) Classic tables: only rows in <tbody> that have at least one <td>
document.querySelectorAll("table").forEach(tbl => {
    tbl.querySelectorAll("tbody tr").forEach(tr => {
        const tds = Array.from(tr.querySelectorAll("td"));
        if (tds.length === 0) return;
        const parts = tds.map(td => (td.innerText || td.textContent || "").trim()).filter(Boolean);
        const line = parts.join(" | ").trim();
        if (line) out.add(line);
    });
});

// 2) WAI-ARIA grids
document.querySelectorAll("[role='row']").forEach(row => {
    const cells = Array.from(row.querySelectorAll("[role='gridcell'], [role='cell']"));
    if (cells.length === 0) return;
    const parts = cells.map(c => (c.innerText || c.textContent || "").trim()).filter(Boolean);
    const line = parts.join(" | ").trim();
    if (line) out.add(line);
});

// 3) AG Grid
document.querySelectorAll(".ag-row").forEach(row => {
    const cells = Array.from(row.querySelectorAll(".ag-cell"));
    if (cells.length === 0) return;
    const parts = cells.map(c => (c.innerText || c.textContent || "").trim()).filter(Boolean);
    const line = parts.join(" | ").trim();
    if (line) out.add(line);
});

return Array.from(out);
"""

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
            # Split by pipe separator
            parts = [p.strip() for p in row_text.split('|')]

            if len(parts) < 2:
                return None

            # First column is usually placement
            placement_text = parts[0]
            placement = self._extract_placement(placement_text)

            if not placement:
                return None

            # Find sailor name (usually 2nd or 3rd column, not a number)
            sailor_name = None
            for part in parts[1:5]:  # Check next few columns
                if part and len(part) > 2 and not part.replace('.', '').isdigit():
                    # Split on newlines if multiple names
                    names = part.split('\n')
                    sailor_name = names[0].strip()
                    break

            if not sailor_name:
                return None

            result_data = {
                'placement': placement,
                'sailor_name': sailor_name,
                'raw_row_data': row_text
            }

            # Try to extract points (usually last column)
            for part in reversed(parts[-3:]):
                try:
                    points = float(part)
                    result_data['points_scored'] = points
                    break
                except ValueError:
                    continue

            return result_data

        except Exception as e:
            logger.debug(f"Error parsing result row: {e}")
            return None

    def _parse_api_regatta_data(self, api_data, regatta_id, url):
        """Parse regatta metadata from Parse API response"""
        data = {
            'source_url': url,
            'external_id': regatta_id,
            'name': api_data.get('name', f'Regatta {regatta_id}')
        }

        # Parse start date
        start_date_obj = api_data.get('startDate', {})
        if isinstance(start_date_obj, dict) and 'iso' in start_date_obj:
            try:
                data['start_date'] = datetime.fromisoformat(
                    start_date_obj['iso'].replace('Z', '+00:00')
                ).date()
            except Exception:
                data['start_date'] = datetime.utcnow().date()
        else:
            data['start_date'] = datetime.utcnow().date()

        # Parse end date
        end_date_obj = api_data.get('endDate', {})
        if isinstance(end_date_obj, dict) and 'iso' in end_date_obj:
            try:
                data['end_date'] = datetime.fromisoformat(
                    end_date_obj['iso'].replace('Z', '+00:00')
                ).date()
            except Exception:
                pass

        # Get club/location from clubObject
        club_obj = api_data.get('clubObject', {})
        if isinstance(club_obj, dict):
            club_name = club_obj.get('name', '')
            if club_name:
                data['location'] = club_name

        return data

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

    @staticmethod
    def _extract_placement(text):
        """Extract numeric placement from text"""
        # Remove common suffixes and extract number
        text = text.replace('st', '').replace('nd', '').replace('rd', '').replace('th', '')
        match = re.search(r'(\d+)', text)
        return int(match.group(1)) if match else None


# One scraper at a time: a second concurrent run would double Chrome's
# memory footprint and fight over the same regattas
_run_lock = Lock()


def run_scraper(limit=None, start_year=2024):
    """
    Convenience function to run the scraper

    Args:
        limit: Max regattas to scrape (default: all available)
        start_year: Only scrape regattas from this year onwards (default: 2024)
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
