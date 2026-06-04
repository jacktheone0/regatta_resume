"""
Web scraper for theclubspot.com regatta results
Uses Parse API to fetch all regatta IDs, then Selenium to scrape results
"""
import requests
from datetime import datetime, timezone
from models import db, Sailor, Regatta, Result, ScraperLog
import logging
import re
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def make_driver():
    """Create a headless Chrome driver optimized for speed"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--disable-extensions")
    options.add_argument("--log-level=3")
    options.page_load_strategy = "eager"
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver


class ClubspotScraper:
    """Scraper for theclubspot.com regatta results"""

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

        # Add year filter if provided
        if start_year:
            start_date = f"{start_year}-01-01T00:00:00.000Z"
            where_clause['startDate'] = {'$gte': {'__type': 'Date', 'iso': start_date}}

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
        log = ScraperLog(status='running')
        db.session.add(log)
        db.session.commit()
        self.log_id = log.id

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

            logger.info(f"Found {len(regattas_data)} regattas to scrape")

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
                logger.info(f"[{idx}/{len(regattas_data)}] Scraping: {regatta_name} ({regatta_id})")

                try:
                    self._scrape_regatta(regatta_id, regatta_data)
                    self.stats['regattas_scraped'] += 1

                    # Update progress in database periodically
                    if idx % 10 == 0:
                        log.regattas_scraped = self.stats['regattas_scraped']
                        log.sailors_added = self.stats['sailors_added']
                        log.results_added = self.stats['results_added']
                        db.session.commit()

                    time.sleep(2)  # Be polite, don't hammer the server
                except Exception as e:
                    logger.error(f"Error scraping {regatta_id}: {e}")
                    continue

            log.status = 'completed'
            log.completed_at = datetime.utcnow()
            log.regattas_scraped = self.stats['regattas_scraped']
            log.sailors_added = self.stats['sailors_added']
            log.results_added = self.stats['results_added']
            db.session.commit()

            logger.info(f"Scraping complete! Stats: {self.stats}")
            return self.stats

        except Exception as e:
            log.status = 'failed'
            log.error_message = str(e)
            log.completed_at = datetime.utcnow()
            db.session.commit()
            logger.error(f"Scraper failed: {e}")
            raise

    def _scrape_regatta(self, regatta_id, api_data=None):
        """
        Scrape a single regatta by ID using Selenium

        Args:
            regatta_id: The clubspot regatta ID
            api_data: Optional dict with regatta metadata from Parse API
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

            # Scrape results from the base regatta URL (ClubSpot shows results there)
            results_data = self._scrape_results_with_selenium(url, regatta.id)

            # Save results to database
            for result_data in results_data:
                self._save_result(result_data, regatta.id)

        except Exception as e:
            logger.error(f"Error in _scrape_regatta for {regatta_id}: {e}")
            raise

    def _scrape_results_with_selenium(self, regatta_url, regatta_id, timeout=20):
        """
        Scrape a ClubSpot regatta page using Selenium.

        ClubSpot results table columns (confirmed structure):
          Place | SAILORS (Skipper\\nCrew) | SAIL NUMBER | BOAT NAME | CLUB/ORG | NET | TOTAL | R1 | R2 ...

        The fleet/class (e.g. "505") is shown in a dropdown above the table, not in the table itself.
        Multiple fleets on the same regatta each need a separate scrape pass.

        Returns:
            List of result dicts — two per row (skipper + crew) when both names are present.
        """
        driver = None
        all_results = []

        # ClubSpot shows results on the base regatta URL, not a /results sub-path
        url = regatta_url.rstrip('/')

        try:
            driver = make_driver()
            driver.get(url)

            try:
                WebDriverWait(driver, timeout).until(
                    lambda d: d.find_elements(By.CSS_SELECTOR, "table tbody tr td")
                )
            except TimeoutException:
                logger.warning(f"Timeout waiting for results table at {url}")
                return []

            # Discover all fleet options from the dropdown (e.g. "505", "Laser")
            fleet_options = self._get_fleet_options(driver)
            if not fleet_options:
                fleet_options = [None]  # single unnamed fleet

            for fleet_name in fleet_options:
                if fleet_name:
                    self._select_fleet(driver, fleet_name)
                    time.sleep(1.5)  # let the table re-render

                # Scroll down to trigger any lazy-loaded rows
                last_height = 0
                for _ in range(20):
                    driver.execute_script("window.scrollBy(0, window.innerHeight);")
                    time.sleep(0.15)
                    new_height = driver.execute_script("return document.body.scrollHeight")
                    if new_height == last_height:
                        break
                    last_height = new_height

                # Extract raw rows via JS — use Array, never Set, to avoid silent dedup
                HARVEST_JS = r"""
const rows = [];
document.querySelectorAll("table tbody tr").forEach(tr => {
    const tds = Array.from(tr.querySelectorAll("td"));
    if (tds.length === 0) return;
    // Keep raw innerText per cell (including newlines for stacked names)
    const cells = tds.map(td => (td.innerText || td.textContent || "").trim());
    rows.push(cells.join(" | "));
});
return rows;
"""
                raw_rows = driver.execute_script(HARVEST_JS) or []
                logger.info(f"Fleet '{fleet_name}': got {len(raw_rows)} raw rows from {url}")

                for raw in raw_rows:
                    parsed = self._parse_result_row(raw, fleet_type=fleet_name)
                    all_results.extend(parsed)  # returns list (skipper + crew)

            logger.info(f"Total parsed results: {len(all_results)} from {url}")
            return all_results

        except Exception as e:
            logger.error(f"Selenium error scraping {url}: {e}")
            return []
        finally:
            if driver:
                driver.quit()

    def _get_fleet_options(self, driver):
        """Return list of fleet names from the fleet dropdown, or [] if none found."""
        try:
            JS = r"""
const btn = document.querySelector('button');
const labels = [];
// Look for a dropdown/select that contains fleet names
document.querySelectorAll('button, [role="option"], option').forEach(el => {
    const txt = (el.innerText || el.textContent || '').trim();
    // Fleet names are short (e.g. "505", "Laser", "FJ") — skip long strings
    if (txt && txt.length < 30 && !txt.toLowerCase().includes('filter') &&
        !txt.toLowerCase().includes('export') && !txt.toLowerCase().includes('print')) {
        labels.push(txt);
    }
});
return [...new Set(labels)];
"""
            candidates = driver.execute_script(JS) or []
            # The first button text on a ClubSpot results page is usually the active fleet
            # Try a more targeted selector for the fleet switcher
            try:
                fleet_btns = driver.find_elements(
                    By.XPATH,
                    "//button[string-length(normalize-space(text())) > 0 and string-length(normalize-space(text())) < 20]"
                )
                fleets = []
                for btn in fleet_btns[:10]:
                    txt = btn.text.strip()
                    if txt and not any(kw in txt.lower() for kw in ['filter', 'export', 'print', 'menu', 'close', 'sign', 'log']):
                        fleets.append(txt)
                if fleets:
                    return fleets[:1]  # start with just the active/first fleet; expand later if needed
            except Exception:
                pass
            return []
        except Exception as e:
            logger.debug(f"Could not read fleet options: {e}")
            return []

    def _select_fleet(self, driver, fleet_name):
        """Click the fleet dropdown option matching fleet_name."""
        try:
            btn = driver.find_element(
                By.XPATH,
                f"//button[normalize-space(text())='{fleet_name}'] | //li[normalize-space(text())='{fleet_name}'] | //*[@role='option'][normalize-space(text())='{fleet_name}']"
            )
            btn.click()
        except Exception as e:
            logger.debug(f"Could not select fleet '{fleet_name}': {e}")

    def _parse_result_row(self, row_text, fleet_type=None):
        """
        Parse one pipe-separated row from a ClubSpot results table.

        Confirmed column order (from live page inspection):
          0: place number (e.g. "1", "2")
          1: sailors — skipper and crew stacked with newline ("Eric Anderson\\nNic Baird")
          2: sail number (e.g. "USA 9248") — stored as crew_partner context, not model field
          3: boat name (e.g. "N = 2") — stored in boat_type field
          4: club / org (e.g. "StFYC") — stored in team_name
          5: NET points (e.g. "6") — stored in points_scored
          6: TOTAL points — skipped
          7+: individual race results (R1, R2 ...) — skipped

        Returns:
            List of dicts — one for skipper, one for crew (when present).
            Empty list if row cannot be parsed.
        """
        try:
            parts = [p.strip() for p in row_text.split('|')]

            if len(parts) < 2:
                return []

            # Column 0: placement
            placement = self._extract_placement(parts[0])
            if not placement:
                return []  # skip header-like or non-numeric rows

            # Column 1: sailors (skipper \n crew)
            sailors_cell = parts[1] if len(parts) > 1 else ''
            names = [n.strip() for n in sailors_cell.split('\n') if n.strip()]
            if not names:
                return []

            skipper = names[0]
            crew = names[1] if len(names) > 1 else None

            # Column 3: boat name → boat_type field
            boat_name = parts[3].strip() if len(parts) > 3 else None
            if boat_name and boat_name.lower() in ('none', ''):
                boat_name = None

            # Column 4: club/org → team_name
            team_name = parts[4].strip() if len(parts) > 4 else None

            # Column 5: NET points → points_scored
            points_scored = None
            if len(parts) > 5:
                try:
                    points_scored = float(parts[5])
                except ValueError:
                    pass

            # Use fleet_type as boat_type if we have it (more meaningful than boat name)
            resolved_boat_type = fleet_type or boat_name

            base = {
                'placement': placement,
                'boat_type': resolved_boat_type,
                'team_name': team_name,
                'points_scored': points_scored,
                'raw_row_data': row_text,
            }

            results = []

            skipper_entry = {**base, 'sailor_name': skipper, 'role': 'skipper', 'crew_partner': crew}
            results.append(skipper_entry)

            if crew:
                crew_entry = {**base, 'sailor_name': crew, 'role': 'crew', 'crew_partner': skipper}
                results.append(crew_entry)

            return results

        except Exception as e:
            logger.debug(f"Error parsing row '{row_text}': {e}")
            return []

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

        sailor = self._get_or_create_sailor(sailor_name)

        # Dedup key includes role so skipper and crew of the same boat are separate records,
        # and a sailor competing in multiple divisions of the same regatta is also allowed.
        role = result_data.get('role', '')
        existing = Result.query.filter_by(
            sailor_id=sailor.id,
            regatta_id=regatta_id,
            role=role
        ).first()

        if existing:
            logger.debug(f"Result already exists: {sailor_name} ({role}) at regatta {regatta_id}")
            return

        result = Result(
            sailor_id=sailor.id,
            regatta_id=regatta_id,
            placement=result_data['placement'],
            boat_type=result_data.get('boat_type'),
            role=role,
            points_scored=result_data.get('points_scored'),
            division=result_data.get('division'),
            team_name=result_data.get('team_name'),
            crew_partner=result_data.get('crew_partner'),
            raw_row_data=result_data.get('raw_row_data'),
        )

        db.session.add(result)
        db.session.commit()
        self.stats['results_added'] += 1
        logger.debug(f"Added result: {sailor_name} ({role}) - place {result_data['placement']}")

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


def run_scraper(limit=None, start_year=2024):
    """
    Convenience function to run the scraper

    Args:
        limit: Max regattas to scrape (default: all available)
        start_year: Only scrape regattas from this year onwards (default: 2024)
    """
    scraper = ClubspotScraper()
    return scraper.scrape_all_regattas(limit=limit, start_year=start_year)


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
