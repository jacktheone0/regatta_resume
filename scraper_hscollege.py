"""
High School and College Sailing Scraper
Scrapes scores.hssailing.org and scores.collegesailing.org

Uses JavaScript extraction to get sailor names and results from individual sailor pages.
Adapted from the ClubSpot scraper framework.
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
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def make_driver():
    """Create headless Chrome driver"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--log-level=3")
    options.page_load_strategy = "eager"
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver


class HSCollegeScraper:
    """Scraper for High School and College sailing sites"""

    def __init__(self, log_id=None):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0'
        })
        self.stats = {
            'regattas_scraped': 0,
            'sailors_added': 0,
            'results_added': 0
        }
        self.log_id = log_id
        self.scraped_sailors = set()  # Track sailors we've already processed
        self.base_url = None

    def should_stop(self):
        """Check if scraper should stop"""
        if not self.log_id:
            return False
        try:
            db.session.expire_all()
            log = ScraperLog.query.get(self.log_id)
            if log and log.status == 'cancelled':
                logger.info("Stop requested, cancelling...")
                return True
        except Exception as e:
            logger.error(f"Error checking stop: {e}")
        return False

    def scrape_all_seasons(self, base_url, seasons, limit=None):
        """
        Scrape multiple seasons from HS or College site

        Args:
            base_url: 'https://scores.hssailing.org' or 'https://scores.collegesailing.org'
            seasons: list of season codes like ['s25', 'f24', 's24']
            limit: max regattas to scrape
        """
        self.base_url = base_url

        log = ScraperLog(status='running')
        db.session.add(log)
        db.session.commit()
        self.log_id = log.id

        try:
            logger.info(f"Starting HS/College scraper for {base_url}")

            all_regattas = []
            for season in seasons:
                if self.should_stop():
                    break

                season_url = f"{base_url}/{season}/"
                logger.info(f"Fetching regattas from {season_url}")

                regattas = self._fetch_season_regattas(season_url, base_url, season)
                all_regattas.extend(regattas)

                if limit and len(all_regattas) >= limit:
                    all_regattas = all_regattas[:limit]
                    break

            logger.info(f"Found {len(all_regattas)} regattas total")

            for idx, regatta_info in enumerate(all_regattas, 1):
                if self.should_stop():
                    log.status = 'cancelled'
                    log.completed_at = datetime.utcnow()
                    db.session.commit()
                    return self.stats

                logger.info(f"[{idx}/{len(all_regattas)}] Scraping: {regatta_info['name']}")

                try:
                    self._scrape_regatta(regatta_info)
                    self.stats['regattas_scraped'] += 1

                    if idx % 10 == 0:
                        log.regattas_scraped = self.stats['regattas_scraped']
                        log.sailors_added = self.stats['sailors_added']
                        log.results_added = self.stats['results_added']
                        db.session.commit()

                    time.sleep(2)
                except Exception as e:
                    logger.error(f"Error scraping {regatta_info['name']}: {e}")
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

    def _fetch_season_regattas(self, season_url, base_url, season_code):
        """Fetch list of regattas from a season page"""
        try:
            response = self.session.get(season_url, timeout=30)
            if response.status_code != 200:
                logger.warning(f"Failed to fetch {season_url}")
                return []

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.content, 'html.parser')

            regattas = []

            # Look for links that end with season code (e.g., /s25/regatta-name/)
            for link in soup.find_all('a', href=True):
                href = link['href']

                # Pattern: /s25/some-regatta-name/ or /f24/another-regatta/
                if f'/{season_code}/' in href and href.count('/') >= 3:
                    regatta_name = link.get_text(strip=True)
                    if regatta_name and len(regatta_name) > 3:
                        full_url = href if href.startswith('http') else base_url + href

                        regattas.append({
                            'name': regatta_name,
                            'url': full_url,
                            'season': season_code
                        })

            # Remove duplicates
            seen = set()
            unique = []
            for r in regattas:
                if r['url'] not in seen:
                    seen.add(r['url'])
                    unique.append(r)

            logger.info(f"Found {len(unique)} regattas in {season_code}")
            return unique

        except Exception as e:
            logger.error(f"Error fetching season {season_url}: {e}")
            return []

    def _scrape_regatta(self, regatta_info):
        """
        Scrape a regatta by getting sailor URLs and scraping their individual pages

        1. Get sailor roster from sailors page
        2. For each sailor, visit their individual page
        3. Extract all their regatta results
        """
        regatta_url = regatta_info['url'].rstrip('/')
        sailors_url = f"{regatta_url}/sailors/"

        # Get sailor URLs from roster
        sailor_urls = self._scrape_sailor_urls(sailors_url)

        if not sailor_urls:
            logger.warning(f"No sailors found for {regatta_info['name']}")
            return

        logger.info(f"Found {len(sailor_urls)} sailors, scraping individual pages...")

        # Scrape each sailor's individual page
        for sailor_url in sailor_urls:
            if self.should_stop():
                return

            # Skip if already scraped
            if sailor_url in self.scraped_sailors:
                continue

            self.scraped_sailors.add(sailor_url)

            try:
                self._scrape_sailor_profile(sailor_url)
                time.sleep(1)
            except Exception as e:
                logger.error(f"Error scraping sailor {sailor_url}: {e}")
                continue

        self.stats['regattas_scraped'] += 1

    def _scrape_sailor_urls(self, sailors_url):
        """
        Extract sailor profile URLs from regatta sailors page
        Uses JavaScript extraction similar to ClubSpot scraper
        """
        driver = None
        try:
            driver = make_driver()
            driver.get(sailors_url)
            time.sleep(2)

            # JavaScript to extract sailor profile links
            JS_EXTRACT_SAILORS = r"""
const sailorUrls = [];
const links = document.querySelectorAll('a[href*="/sailors/"]');

links.forEach(link => {
    const href = link.getAttribute('href');
    if (href && href.includes('/sailors/') && !href.endsWith('/sailors/')) {
        const fullUrl = href.startsWith('http') ? href : window.location.origin + href;
        sailorUrls.push(fullUrl);
    }
});

// Remove duplicates
return [...new Set(sailorUrls)];
"""

            sailor_urls = driver.execute_script(JS_EXTRACT_SAILORS) or []
            logger.info(f"Found {len(sailor_urls)} sailor URLs at {sailors_url}")
            return sailor_urls

        except Exception as e:
            logger.error(f"Error scraping sailor URLs from {sailors_url}: {e}")
            return []
        finally:
            if driver:
                driver.quit()

    def _scrape_sailor_profile(self, sailor_url):
        """
        Scrape an individual sailor's profile page
        Uses HARVEST_JS style extraction to get regatta results as pipe-separated strings
        """
        driver = None
        try:
            driver = make_driver()
            driver.get(sailor_url)
            time.sleep(2)

            # JavaScript to extract sailor info and regatta results
            # Similar to ClubSpot's HARVEST_JS - extracts data as pipe-separated strings
            JS_HARVEST_SAILOR = r"""
const data = {
    sailor_name: '',
    results: []
};

// Get sailor name from h1 or title
const nameElem = document.querySelector('h1');
if (nameElem) {
    data.sailor_name = (nameElem.innerText || '').trim();
}

// Extract regatta results from tables
const rows = document.querySelectorAll('table tbody tr, table tr');

rows.forEach(row => {
    const cells = Array.from(row.querySelectorAll('td, th'));
    if (cells.length < 2) return;

    // Extract cell text as pipe-separated string (like ClubSpot scraper)
    const cellTexts = cells.map(c => (c.innerText || '').trim());
    const rowText = cellTexts.join(' | ');

    // Check if this row has a regatta link
    const regattaLink = row.querySelector('a[href*="/s2"], a[href*="/f2"]');
    if (regattaLink && rowText) {
        const regattaName = regattaLink.innerText.trim();
        const regattaUrl = regattaLink.getAttribute('href');

        data.results.push({
            raw_row: rowText,
            regatta_name: regattaName,
            regatta_url: regattaUrl
        });
    }
});

return data;
"""

            profile_data = driver.execute_script(JS_HARVEST_SAILOR) or {}

            sailor_name = profile_data.get('sailor_name', '')
            # Clean up name (remove "- Sailor Profile" etc)
            sailor_name = re.sub(r'\s*[-–]\s*Sailor.*$', '', sailor_name, flags=re.IGNORECASE).strip()

            if not sailor_name:
                logger.warning(f"Could not extract sailor name from {sailor_url}")
                return

            # Create sailor record
            sailor = self._get_or_create_sailor(sailor_name)

            results = profile_data.get('results', [])
            logger.info(f"Found {len(results)} results for {sailor_name}")

            # Parse each result row (similar to ClubSpot's _parse_result_row)
            for result_raw in results:
                try:
                    result_data = self._parse_sailor_result_row(
                        result_raw['raw_row'],
                        result_raw['regatta_name'],
                        result_raw['regatta_url']
                    )
                    if result_data:
                        self._save_sailor_result(sailor, result_data)
                except Exception as e:
                    logger.debug(f"Error parsing result for {sailor_name}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error scraping sailor profile from {sailor_url}: {e}")
        finally:
            if driver:
                driver.quit()

    def _parse_sailor_result_row(self, row_text, regatta_name, regatta_url):
        """
        Parse a single result row to extract placement and other data
        Similar to ClubSpot's _parse_result_row but adapted for HS/College format

        Args:
            row_text: Pipe-separated row (e.g., "Fall Dinghy | 21/32 (B Div) | Skipper | MIT")
            regatta_name: Name of regatta from link
            regatta_url: URL to regatta page

        Returns:
            Dict with regatta info, placement, division, role, raw_data
        """
        try:
            parts = [p.strip() for p in row_text.split('|')]

            # Look for placement in format "21/32" or "21/32 (B Div)"
            placement = None
            division = None
            role = None

            for part in parts:
                # Match "21/32 (B Div)" or "21/32"
                finish_match = re.search(r'(\d+)/(\d+)\s*(\([^)]+\))?', part)
                if finish_match:
                    placement = int(finish_match.group(1))
                    # Extract division from "(B Div)"
                    div_match = re.search(r'\(([^)]+)\)', part)
                    if div_match:
                        division = div_match.group(1)

                # Detect role
                part_lower = part.lower()
                if 'skipper' in part_lower:
                    role = 'Skipper'
                elif 'crew' in part_lower:
                    role = 'Crew'

            if not placement:
                return None

            # Construct full regatta URL
            if regatta_url and not regatta_url.startswith('http'):
                regatta_url = self.base_url + regatta_url

            # Extract external ID from URL
            if regatta_url:
                external_id = regatta_url.rstrip('/').split('/')[-1]
            else:
                external_id = re.sub(r'[^a-z0-9]+', '-', regatta_name.lower()).strip('-')

            return {
                'regatta_name': regatta_name,
                'regatta_url': regatta_url,
                'external_id': external_id,
                'placement': placement,
                'division': division,
                'role': role,
                'raw_row_data': row_text
            }

        except Exception as e:
            logger.debug(f"Error parsing result row: {e}")
            return None

    def _save_sailor_result(self, sailor, result_data):
        """Save a single regatta result for a sailor"""
        # Get or create regatta
        regatta_data = {
            'name': result_data['regatta_name'],
            'source_url': result_data['regatta_url'],
            'external_id': result_data['external_id'],
            'start_date': datetime.utcnow().date(),
        }
        regatta = self._get_or_create_regatta(regatta_data)

        # Check if result already exists
        existing = Result.query.filter_by(
            sailor_id=sailor.id,
            regatta_id=regatta.id
        ).first()

        if existing:
            return

        # Create result record
        result = Result(
            sailor_id=sailor.id,
            regatta_id=regatta.id,
            placement=result_data['placement'],
            division=result_data.get('division'),
            role=result_data.get('role'),
            raw_row_data=result_data.get('raw_row_data')
        )

        db.session.add(result)
        db.session.commit()
        self.stats['results_added'] += 1

    def _get_or_create_sailor(self, name):
        """Get or create sailor"""
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

        return sailor

    def _get_or_create_regatta(self, data):
        """Get or create regatta"""
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

        return regatta


def run_hs_scraper(limit=None, seasons=None):
    """
    Run High School sailing scraper

    Args:
        limit: Max regattas to scrape
        seasons: List like ['s25', 'f24'] (default: current season)
    """
    scraper = HSCollegeScraper()
    if not seasons:
        seasons = ['s25']  # Current season

    return scraper.scrape_all_seasons(
        'https://scores.hssailing.org',
        seasons,
        limit=limit
    )


def run_college_scraper(limit=None, seasons=None):
    """
    Run College sailing scraper

    Args:
        limit: Max regattas to scrape
        seasons: List like ['s26', 'f25'] (default: current season)
    """
    scraper = HSCollegeScraper()
    if not seasons:
        seasons = ['s26']  # Current season

    return scraper.scrape_all_seasons(
        'https://scores.collegesailing.org',
        seasons,
        limit=limit
    )
