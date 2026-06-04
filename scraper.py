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

        Confirmed DOM structure (from live page inspection):
          <tr class="tableRow resultsRow ...">
            <td class="first-cell">        ← placement number
            <td class="sticky-column">     ← sailor name(s); two-handed boats stack Skipper\\nCrew
            <td class="print_smallPadding"> ← sail number
            <td class="print_smallPadding"> ← boat name ("None" when absent)
            <td class="print_smallPadding"> ← club/org
            <td class="print_smallPadding"> ← NET points
            <td class="print_smallPadding"> ← TOTAL points
            <td class="print_smallPadding"> ← R1, R2, ... (individual race results)

        Multiple fleets (e.g. ILCA 6, ILCA 7) are rendered as separate tables on the same
        page simultaneously — not hidden behind a dropdown. Each table is preceded by a
        fleet-label element. The JS below discovers fleet context per table.

        Returns:
            List of result dicts — two per row for two-handed boats (skipper + crew),
            one per row for single-handed.
        """
        driver = None
        all_results = []

        url = regatta_url.rstrip('/')

        try:
            driver = make_driver()
            driver.get(url)

            try:
                WebDriverWait(driver, timeout).until(
                    lambda d: d.find_elements(By.CSS_SELECTOR, "table tbody tr td.first-cell")
                )
            except TimeoutException:
                logger.warning(f"Timeout waiting for results table at {url}")
                return []

            # Scroll until page stops growing to load all lazy-rendered rows
            last_height = 0
            for _ in range(30):
                driver.execute_script("window.scrollBy(0, window.innerHeight);")
                time.sleep(0.15)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height

            # Extract rows from all tables on the page.
            # Each table gets its fleet name from the nearest preceding short-text element
            # (e.g. "ILCA 6", "ILCA 7", "505"). We append it as a sentinel field.
            HARVEST_JS = r"""
const results = [];
document.querySelectorAll("table").forEach(function(table) {
    // Walk backwards/up in the DOM to find a short fleet label near this table
    var fleetName = "";
    try {
        var el = table;
        for (var depth = 0; depth < 6; depth++) {
            el = el.parentElement;
            if (!el || el === document.body) break;
            var prev = el.previousElementSibling;
            if (prev) {
                var txt = (prev.innerText || prev.textContent || "").trim().split("\n")[0].trim();
                if (txt && txt.length > 0 && txt.length <= 20) {
                    fleetName = txt;
                    break;
                }
            }
        }
    } catch(e) {}

    table.querySelectorAll("tbody tr").forEach(function(tr) {
        // Use class-specific selectors for reliability
        var placementCell = tr.querySelector("td.first-cell");
        var sailorCell    = tr.querySelector("td.sticky-column");
        if (!placementCell || !sailorCell) return;

        var allTds = Array.from(tr.querySelectorAll("td"));
        var cells = allTds.map(function(td) {
            return (td.innerText || td.textContent || "").trim();
        });
        // Append fleet sentinel so the parser knows which fleet this row belongs to
        cells.push("__fleet__" + fleetName);
        results.push(cells.join(" | "));
    });
});
return results;
"""
            raw_rows = driver.execute_script(HARVEST_JS) or []
            logger.info(f"Got {len(raw_rows)} raw rows from {url}")

            for raw in raw_rows:
                parsed = self._parse_result_row(raw)
                all_results.extend(parsed)

            logger.info(f"Total parsed results: {len(all_results)} from {url}")
            return all_results

        except Exception as e:
            logger.error(f"Selenium error scraping {url}: {e}")
            return []
        finally:
            if driver:
                driver.quit()

    def _parse_result_row(self, row_text):
        """
        Parse one pipe-separated row emitted by HARVEST_JS.

        Confirmed ClubSpot column order (indices after splitting on ' | '):
          0  — placement        (from td.first-cell)
          1  — sailor name(s)   (from td.sticky-column; two-handed boats have Skipper\\nCrew)
          2  — sail number      (ignored)
          3  — boat name        ("None" when absent)
          4  — club / org       → stored as team_name
          5  — NET points       → stored as points_scored
          6  — TOTAL points     (ignored)
          7+ — R1, R2, ...      (individual race results, ignored)
          last — "__fleet__<name>" sentinel appended by HARVEST_JS

        Returns:
            List of dicts — one entry per sailor (two for two-handed boats, one for single-handed).
            Empty list if the row cannot be meaningfully parsed.
        """
        try:
            parts = [p.strip() for p in row_text.split(' | ')]

            if len(parts) < 2:
                return []

            # Strip fleet sentinel from the end
            fleet_type = None
            if parts[-1].startswith('__fleet__'):
                fleet_type = parts[-1][len('__fleet__'):].strip() or None
                parts = parts[:-1]

            # Column 0: placement (must be a positive integer)
            placement = self._extract_placement(parts[0])
            if not placement:
                return []

            # Column 1: sailor name(s) — single-handed has one name, two-handed has Skipper\nCrew
            sailors_cell = parts[1] if len(parts) > 1 else ''
            names = [n.strip() for n in sailors_cell.split('\n') if n.strip()]
            if not names:
                return []

            skipper = names[0]
            crew = names[1] if len(names) > 1 else None

            # Column 3: boat name (skip ClubSpot's "None" placeholder)
            boat_name = parts[3].strip() if len(parts) > 3 else None
            if not boat_name or boat_name.lower() == 'none':
                boat_name = None

            # Column 4: club / org
            team_name = parts[4].strip() if len(parts) > 4 else None

            # Column 5: NET points
            points_scored = None
            if len(parts) > 5:
                try:
                    points_scored = float(parts[5])
                except ValueError:
                    pass

            # Fleet class (e.g. "ILCA 6", "505") is more useful as boat_type than the boat name
            resolved_boat_type = fleet_type or boat_name

            base = {
                'placement': placement,
                'boat_type': resolved_boat_type,
                'team_name': team_name,
                'points_scored': points_scored,
                'raw_row_data': row_text,
            }

            results = []
            results.append({**base, 'sailor_name': skipper, 'role': 'skipper', 'crew_partner': crew})
            if crew:
                results.append({**base, 'sailor_name': crew, 'role': 'crew', 'crew_partner': skipper})

            return results

        except Exception as e:
            logger.debug(f"Error parsing row: {e}")
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
