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

            # Now scrape results using Selenium
            results_url = f"{url}/results?list_view=true"
            results_data = self._scrape_results_with_selenium(results_url, regatta.id)

            # Save results to database
            for result_data in results_data:
                self._save_result(result_data, regatta.id)

        except Exception as e:
            logger.error(f"Error in _scrape_regatta for {regatta_id}: {e}")
            raise

    def _scrape_results_with_selenium(self, results_url, regatta_id, timeout=12):
        """
        Scrape results page using Selenium with header-based column mapping
        Maps columns by name (SAILORS, NET, etc.) instead of assuming positions

        Args:
            results_url: URL of the results page
            regatta_id: Database ID of the regatta
            timeout: Seconds to wait for page to load

        Returns:
            List of result dicts with sailor names, placements, and points
        """
        driver = None
        results = []

        try:
            driver = make_driver()
            driver.get(results_url)

            # Wait for table to load
            def any_rows_present(d):
                if d.find_elements(By.CSS_SELECTOR, "table tbody tr td"):
                    return True
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

            # Use the inspect table structure logic to get headers and rows
            table_structure = driver.execute_script(self._get_table_extraction_js())

            if not table_structure or not table_structure.get('rows'):
                logger.warning(f"No table data found at {results_url}")
                return []

            headers = table_structure.get('headers', [])
            rows = table_structure.get('rows', [])

            logger.info(f"Found {len(rows)} rows with {len(headers)} columns")
            logger.info(f"Headers: {headers}")

            # Map column indices by header name (case-insensitive)
            col_map = self._map_column_indices(headers)
            logger.info(f"Column mapping: {col_map}")

            # Parse each row using header-based mapping
            for row_idx, row_data in enumerate(rows, 1):
                result_data = self._parse_row_with_headers(row_data, row_idx, col_map)
                if result_data:
                    results.append(result_data)

            logger.info(f"Extracted {len(results)} valid results from {results_url}")
            return results

        except Exception as e:
            logger.error(f"Error scraping results with Selenium: {e}")
            return []
        finally:
            if driver:
                driver.quit()

    def _get_table_extraction_js(self):
        """JavaScript to extract table headers and rows"""
        return r"""
const result = {
    headers: [],
    rows: [],
    tableType: 'unknown'
};

// Try to find classic HTML table first
const classicTable = document.querySelector('table');
if (classicTable) {
    result.tableType = 'classic-html-table';

    // Get headers from thead or first row
    const headerRow = classicTable.querySelector('thead tr') || classicTable.querySelector('tr');
    if (headerRow) {
        const headers = Array.from(headerRow.querySelectorAll('th, td'));
        result.headers = headers.map(h => (h.innerText || h.textContent || "").trim()).filter(Boolean);
    }

    // Get data rows from tbody
    const dataRows = classicTable.querySelectorAll('tbody tr');
    dataRows.forEach((tr) => {
        const cells = Array.from(tr.querySelectorAll('td'));
        const rowData = cells.map(td => (td.innerText || td.textContent || "").trim());
        if (rowData.some(c => c)) result.rows.push(rowData);
    });
}

// Try AG Grid if no classic table found
if (result.rows.length === 0) {
    const agHeader = document.querySelector('.ag-header-row');
    const agRows = document.querySelectorAll('.ag-row');

    if (agHeader && agRows.length > 0) {
        result.tableType = 'ag-grid';
        const headerCells = agHeader.querySelectorAll('.ag-header-cell');
        result.headers = Array.from(headerCells).map(h => (h.innerText || h.textContent || "").trim()).filter(Boolean);

        agRows.forEach((row) => {
            const cells = Array.from(row.querySelectorAll('.ag-cell'));
            const rowData = cells.map(c => (c.innerText || c.textContent || "").trim());
            if (rowData.some(c => c)) result.rows.push(rowData);
        });
    }
}

return result;
"""

    def _map_column_indices(self, headers):
        """
        Map column indices by header name
        Returns dict with column indices for: sailors, net, total, sail_number
        """
        col_map = {}
        headers_lower = [h.lower() for h in headers]

        # Find SAILORS column
        for idx, h in enumerate(headers_lower):
            if 'sailor' in h or 'name' in h or 'skipper' in h or 'helm' in h:
                col_map['sailors'] = idx
                break

        # Find NET points column (preferred over TOTAL)
        for idx, h in enumerate(headers_lower):
            if h == 'net' or h == 'net points':
                col_map['points'] = idx
                break

        # Fallback to TOTAL if NET not found
        if 'points' not in col_map:
            for idx, h in enumerate(headers_lower):
                if 'total' in h and 'point' in h:
                    col_map['points'] = idx
                    break

        # Find SAIL NUMBER column (optional, for reference)
        for idx, h in enumerate(headers_lower):
            if 'sail' in h and ('number' in h or 'no' in h or '#' in h):
                col_map['sail_number'] = idx
                break

        return col_map

    def _parse_row_with_headers(self, row_data, row_index, col_map):
        """
        Parse a single row using column header mapping
        row_index is the placement (1st, 2nd, 3rd, etc.)
        """
        try:
            # Get sailor names from SAILORS column
            sailor_name = None
            if 'sailors' in col_map:
                sailor_col = row_data[col_map['sailors']]
                # Split by newline if multiple names (skipper/crew)
                names = [n.strip() for n in sailor_col.split('\n') if n.strip()]
                if names:
                    sailor_name = names[0]  # Take first name (skipper)

            if not sailor_name or len(sailor_name) < 2:
                return None

            # Validate it's not a number (sail number leaked into name)
            if sailor_name.replace(' ', '').replace('-', '').isdigit():
                return None

            result_data = {
                'placement': row_index,  # Row index IS the placement
                'sailor_name': sailor_name
            }

            # Get points from NET or TOTAL column
            if 'points' in col_map:
                points_text = row_data[col_map['points']]
                try:
                    points = float(points_text)
                    # Validate reasonable points range (1-1000)
                    if 1 <= points <= 1000:
                        result_data['points_scored'] = points
                except ValueError:
                    pass

            logger.debug(f"Parsed row {row_index}: {sailor_name} = {result_data.get('points_scored', 'N/A')} pts")
            return result_data

        except Exception as e:
            logger.debug(f"Error parsing row {row_index}: {e}")
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
            team_name=result_data.get('team_name')
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

    @staticmethod
    def inspect_table_structure(regatta_id, timeout=12):
        """
        Inspect the table structure of a regatta's results page
        Returns headers and sample rows to help with column mapping

        Args:
            regatta_id: The clubspot regatta ID
            timeout: Seconds to wait for page to load

        Returns:
            Dict with 'headers', 'sample_rows', 'url'
        """
        driver = None
        try:
            driver = make_driver()
            results_url = f"https://theclubspot.com/regatta/{regatta_id}/results?list_view=true"
            driver.get(results_url)

            # Wait for table to load
            def any_rows_present(d):
                if d.find_elements(By.CSS_SELECTOR, "table tbody tr td"):
                    return True
                if d.find_elements(By.CSS_SELECTOR, "[role='row'] [role='gridcell'], .ag-row .ag-cell"):
                    return True
                return False

            try:
                WebDriverWait(driver, timeout).until(any_rows_present)
            except TimeoutException:
                return {
                    'error': 'Timeout waiting for table to load',
                    'url': results_url
                }

            # JavaScript to extract headers AND rows
            INSPECT_JS = r"""
const result = {
    headers: [],
    rows: [],
    tableType: 'unknown'
};

// Try to find classic HTML table first
const classicTable = document.querySelector('table');
if (classicTable) {
    result.tableType = 'classic-html-table';

    // Get headers from thead or first row
    const headerRow = classicTable.querySelector('thead tr') || classicTable.querySelector('tr');
    if (headerRow) {
        const headers = Array.from(headerRow.querySelectorAll('th, td'));
        result.headers = headers.map(h => (h.innerText || h.textContent || "").trim()).filter(Boolean);
    }

    // Get data rows from tbody
    const dataRows = classicTable.querySelectorAll('tbody tr');
    dataRows.forEach((tr, idx) => {
        if (idx < 10) { // Only get first 10 rows
            const cells = Array.from(tr.querySelectorAll('td'));
            const rowData = cells.map(td => (td.innerText || td.textContent || "").trim());
            if (rowData.some(c => c)) result.rows.push(rowData);
        }
    });
}

// Try AG Grid
if (result.rows.length === 0) {
    const agHeader = document.querySelector('.ag-header-row');
    const agRows = document.querySelectorAll('.ag-row');

    if (agHeader && agRows.length > 0) {
        result.tableType = 'ag-grid';

        // Get headers
        const headerCells = agHeader.querySelectorAll('.ag-header-cell');
        result.headers = Array.from(headerCells).map(h => (h.innerText || h.textContent || "").trim()).filter(Boolean);

        // Get rows
        agRows.forEach((row, idx) => {
            if (idx < 10) {
                const cells = Array.from(row.querySelectorAll('.ag-cell'));
                const rowData = cells.map(c => (c.innerText || c.textContent || "").trim());
                if (rowData.some(c => c)) result.rows.push(rowData);
            }
        });
    }
}

// Try ARIA grids
if (result.rows.length === 0) {
    const allRows = Array.from(document.querySelectorAll('[role="row"]'));
    if (allRows.length > 0) {
        result.tableType = 'aria-grid';

        // First row might be headers
        const firstRow = allRows[0];
        const firstCells = firstRow.querySelectorAll('[role="columnheader"], [role="gridcell"], [role="cell"]');
        const firstText = Array.from(firstCells).map(c => (c.innerText || c.textContent || "").trim());

        // Check if first row looks like headers (contains words, not numbers)
        const looksLikeHeader = firstText.some(t => t && /[a-zA-Z]/.test(t) && t.length > 2);
        if (looksLikeHeader) {
            result.headers = firstText;
        }

        // Get data rows
        allRows.forEach((row, idx) => {
            if ((looksLikeHeader && idx === 0) || idx >= 10) return; // Skip header row or if we have enough
            const cells = row.querySelectorAll('[role="gridcell"], [role="cell"]');
            const rowData = Array.from(cells).map(c => (c.innerText || c.textContent || "").trim());
            if (rowData.some(c => c)) result.rows.push(rowData);
        });
    }
}

return result;
"""

            # Scroll to load content
            for _ in range(8):
                driver.execute_script("window.scrollBy(0, Math.max(600, window.innerHeight));")
                time.sleep(0.2)

            # Extract table structure
            table_data = driver.execute_script(INSPECT_JS)

            return {
                'url': results_url,
                'regatta_id': regatta_id,
                'table_type': table_data.get('tableType', 'unknown'),
                'headers': table_data.get('headers', []),
                'sample_rows': table_data.get('rows', []),
                'num_columns': len(table_data.get('headers', [])),
                'num_sample_rows': len(table_data.get('rows', []))
            }

        except Exception as e:
            return {
                'error': str(e),
                'url': f"https://theclubspot.com/regatta/{regatta_id}/results?list_view=true"
            }
        finally:
            if driver:
                driver.quit()


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
