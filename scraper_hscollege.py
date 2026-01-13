"""
High School and College Sailing Scraper
Scrapes scores.hssailing.org and scores.collegesailing.org

Uses individual sailor pages to get accurate per-regatta placements.
Extracts placement data like "21/32 (B Div)" - 21st out of 32 boats in B Division.
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
from bs4 import BeautifulSoup

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
        self.base_url = None  # Will be set in scrape_all_seasons

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
        self.base_url = base_url  # Store for sailor URL construction

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

            soup = BeautifulSoup(response.content, 'html.parser')

            # Find all regatta links (they're usually in a list or table)
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
        Scrape a single regatta by discovering sailors and scraping their individual pages

        1. Get sailor roster from sailors page
        2. For each sailor, visit their individual page (if not already scraped)
        3. Extract all their regatta results from their profile
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
                time.sleep(1)  # Be nice to the server
            except Exception as e:
                logger.error(f"Error scraping sailor {sailor_url}: {e}")
                continue

        self.stats['regattas_scraped'] += 1

    def _scrape_sailor_urls(self, sailors_url):
        """
        Scrape sailor URLs from a regatta's sailors roster page
        Returns list of sailor profile URLs
        """
        driver = None
        try:
            driver = make_driver()
            driver.get(sailors_url)

            time.sleep(2)

            # Extract sailor profile links
            js = r"""
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

            sailor_urls = driver.execute_script(js) or []
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
        Scrape an individual sailor's profile page to extract all their regatta results

        Individual sailor pages show chronological regatta history with:
        - Regatta name and link
        - Placement in format "21/32 (B Div)" - 21st out of 32 boats in B Division
        - Role (skipper or crew)
        """
        driver = None
        try:
            driver = make_driver()
            driver.get(sailor_url)

            time.sleep(2)

            # Extract sailor info and regatta results
            js = r"""
            const data = {
                sailor_name: '',
                school: '',
                grad_year: '',
                regattas: []
            };

            // Get sailor name from page title or h1
            const nameElem = document.querySelector('h1, title');
            if (nameElem) {
                data.sailor_name = (nameElem.innerText || nameElem.textContent || '').trim();
            }

            // Get school and grad year (usually in a summary section)
            const summaryText = document.body.innerText;
            const gradMatch = summaryText.match(/Class of (\d{4})/i);
            if (gradMatch) data.grad_year = gradMatch[1];

            // Extract regatta results from tables or lists
            // Format: rows with regatta name, finish like "21/32 (B Div)", role
            const rows = document.querySelectorAll('table tbody tr, table tr, .regatta-row');

            rows.forEach(row => {
                const cells = Array.from(row.querySelectorAll('td, th, div'));
                if (cells.length < 2) return;

                const cellTexts = cells.map(c => (c.innerText || c.textContent || '').trim());

                // Look for regatta link
                const regattaLink = row.querySelector('a[href*="/s2"], a[href*="/f2"]');
                if (!regattaLink) return;

                const regattaName = regattaLink.innerText.trim();
                const regattaUrl = regattaLink.getAttribute('href');

                // Look for finish like "21/32 (B Div)" or "21/32"
                let finish = '';
                let division = '';
                let role = '';

                cellTexts.forEach(text => {
                    // Match "21/32 (B Div)" or "21/32"
                    const finishMatch = text.match(/(\d+)\/(\d+)\s*(\([^)]+\))?/);
                    if (finishMatch) {
                        finish = text;
                        const divMatch = text.match(/\(([^)]+)\)/);
                        if (divMatch) division = divMatch[1];
                    }

                    // Detect role
                    if (text.toLowerCase() === 'skipper' || text.toLowerCase() === 'crew') {
                        role = text;
                    }
                });

                if (regattaName && finish) {
                    data.regattas.push({
                        name: regattaName,
                        url: regattaUrl,
                        finish: finish,
                        division: division,
                        role: role,
                        raw_data: cellTexts.join(' | ')
                    });
                }
            });

            return data;
            """

            profile_data = driver.execute_script(js) or {}

            if not profile_data.get('sailor_name'):
                logger.warning(f"Could not extract sailor name from {sailor_url}")
                return

            sailor_name = profile_data['sailor_name']
            # Clean up name (remove "- Sailor Profile" etc)
            sailor_name = re.sub(r'\s*[-–]\s*Sailor.*$', '', sailor_name, flags=re.IGNORECASE).strip()

            if not sailor_name:
                return

            # Create sailor record
            sailor = self._get_or_create_sailor(sailor_name)

            logger.info(f"Found {len(profile_data.get('regattas', []))} results for {sailor_name}")

            # Save each regatta result
            for regatta_result in profile_data.get('regattas', []):
                try:
                    self._save_sailor_result(sailor, regatta_result)
                except Exception as e:
                    logger.error(f"Error saving result for {sailor_name}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error scraping sailor profile from {sailor_url}: {e}")
        finally:
            if driver:
                driver.quit()

    def _save_sailor_result(self, sailor, regatta_result):
        """
        Save a single regatta result for a sailor

        Args:
            sailor: Sailor object
            regatta_result: dict with 'name', 'url', 'finish', 'division', 'role', 'raw_data'
        """
        regatta_name = regatta_result.get('name')
        regatta_url = regatta_result.get('url')

        if not regatta_name:
            return

        # Construct full URL if needed
        if regatta_url and not regatta_url.startswith('http'):
            regatta_url = self.base_url + regatta_url

        # Extract external ID from URL (slug at end)
        if regatta_url:
            external_id = regatta_url.rstrip('/').split('/')[-1]
        else:
            # Fallback: use slugified name
            external_id = re.sub(r'[^a-z0-9]+', '-', regatta_name.lower()).strip('-')

        # Get or create regatta
        regatta_data = {
            'name': regatta_name,
            'source_url': regatta_url,
            'external_id': external_id,
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

        # Parse placement from finish like "21/32 (B Div)"
        finish = regatta_result.get('finish', '')
        placement = None
        if finish:
            match = re.match(r'(\d+)/(\d+)', finish)
            if match:
                placement = int(match.group(1))  # Extract "21" from "21/32"

        # Create result record
        result = Result(
            sailor_id=sailor.id,
            regatta_id=regatta.id,
            placement=placement,
            division=regatta_result.get('division'),
            role=regatta_result.get('role'),
            raw_row_data=regatta_result.get('raw_data')
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
        seasons: List like ['s25', 'f24'] (default: current season)
    """
    scraper = HSCollegeScraper()
    if not seasons:
        seasons = ['s26']  # Current season

    return scraper.scrape_all_seasons(
        'https://scores.collegesailing.org',
        seasons,
        limit=limit
    )
