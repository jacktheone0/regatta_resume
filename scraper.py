"""
Web scraper for theclubspot.com regatta results
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from models import db, Sailor, Regatta, Result, ScraperLog
from sqlalchemy.exc import IntegrityError
import logging
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ClubspotScraper:
    """Scraper for theclubspot.com regatta results"""

    def __init__(self, base_url='https://www.theclubspot.com', user_agent=None):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': user_agent or 'Mozilla/5.0 (compatible; RegattaResume/1.0)'
        })
        self.stats = {
            'regattas_scraped': 0,
            'sailors_added': 0,
            'results_added': 0
        }

    def scrape_all_regattas(self, limit=None):
        """
        Main entry point: scrape all available regattas

        Args:
            limit: Maximum number of regattas to scrape (for testing)

        Returns:
            dict with scraping statistics
        """
        log = ScraperLog(status='running')
        db.session.add(log)
        db.session.commit()

        try:
            logger.info("Starting scraper...")
            regatta_links = self._get_regatta_list(limit=limit)
            logger.info(f"Found {len(regatta_links)} regattas to scrape")

            for idx, regatta_url in enumerate(regatta_links, 1):
                logger.info(f"[{idx}/{len(regatta_links)}] Scraping: {regatta_url}")
                try:
                    self._scrape_regatta(regatta_url)
                    self.stats['regattas_scraped'] += 1
                except Exception as e:
                    logger.error(f"Error scraping {regatta_url}: {e}")
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

    def _get_regatta_list(self, limit=None):
        """
        Get list of regatta URLs from theclubspot.com

        This is a placeholder - you'll need to customize based on the actual
        site structure. Common patterns:
        - /regattas/ or /events/ listing page
        - Search/filter interface
        - Archives by year
        """
        regatta_urls = []

        # Example structure - adjust based on actual site
        # Option 1: Direct results pages
        # urls = [f"{self.base_url}/regatta/{i}" for i in range(1, 100)]

        # Option 2: Scrape from a listing page
        try:
            # Try to find regattas listing page
            response = self.session.get(f"{self.base_url}/regattas")
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'lxml')

                # Find all regatta links - adjust selector based on actual HTML
                links = soup.select('a[href*="/regatta/"]')
                for link in links:
                    href = link.get('href')
                    if href and href.startswith('/'):
                        href = self.base_url + href
                    if href and href not in regatta_urls:
                        regatta_urls.append(href)

        except Exception as e:
            logger.warning(f"Could not fetch regatta list: {e}")
            # Fallback: try common URL patterns
            logger.info("Trying fallback URL patterns...")

        if limit:
            regatta_urls = regatta_urls[:limit]

        return regatta_urls

    def _scrape_regatta(self, url):
        """
        Scrape a single regatta page for results

        Args:
            url: URL of the regatta results page
        """
        response = self.session.get(url)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch {url}: {response.status_code}")
            return

        soup = BeautifulSoup(response.content, 'lxml')

        # Extract regatta metadata
        regatta_data = self._extract_regatta_metadata(soup, url)
        regatta = self._get_or_create_regatta(regatta_data)

        # Extract results table
        results_data = self._extract_results(soup, regatta.id)

        # Save results to database
        for result_data in results_data:
            self._save_result(result_data, regatta.id)

    def _extract_regatta_metadata(self, soup, url):
        """
        Extract regatta information from the page

        Customize this based on theclubspot.com HTML structure
        """
        data = {
            'source_url': url,
            'external_id': self._extract_external_id(url)
        }

        # Try to find regatta name - common selectors
        name_elem = (
            soup.select_one('h1.event-title') or
            soup.select_one('h1.regatta-name') or
            soup.select_one('.event-header h1') or
            soup.select_one('h1')
        )
        data['name'] = name_elem.get_text(strip=True) if name_elem else 'Unknown Regatta'

        # Try to find location
        location_elem = (
            soup.select_one('.event-location') or
            soup.select_one('.location') or
            soup.find(string=re.compile(r'Location:', re.I))
        )
        if location_elem:
            data['location'] = location_elem.get_text(strip=True).replace('Location:', '').strip()

        # Try to find dates
        date_elem = (
            soup.select_one('.event-date') or
            soup.select_one('.date') or
            soup.find(string=re.compile(r'\d{1,2}/\d{1,2}/\d{4}'))
        )
        if date_elem:
            date_text = date_elem.get_text(strip=True) if hasattr(date_elem, 'get_text') else str(date_elem)
            data['start_date'] = self._parse_date(date_text)

        # Try to find fleet type
        fleet_elem = (
            soup.select_one('.fleet-type') or
            soup.select_one('.boat-class') or
            soup.find(string=re.compile(r'Fleet:|Class:', re.I))
        )
        if fleet_elem:
            data['fleet_type'] = fleet_elem.get_text(strip=True).replace('Fleet:', '').replace('Class:', '').strip()

        return data

    def _extract_results(self, soup, regatta_id):
        """
        Extract results table from the page

        Returns list of dicts with sailor results
        """
        results = []

        # Find the results table - adjust selector based on actual HTML
        table = (
            soup.select_one('table.results') or
            soup.select_one('table.standings') or
            soup.select_one('.results-table table') or
            soup.find('table')
        )

        if not table:
            logger.warning(f"No results table found for regatta {regatta_id}")
            return results

        rows = table.select('tbody tr') if table.select('tbody') else table.select('tr')[1:]

        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 2:
                continue

            # Extract data from cells - adjust indices based on actual table structure
            # Common structure: Rank | Sailor | Team | Division | Points
            try:
                result_data = {
                    'placement': self._extract_placement(cells[0].get_text(strip=True)),
                    'sailor_name': cells[1].get_text(strip=True) if len(cells) > 1 else None,
                }

                # Optional fields - adjust based on table structure
                if len(cells) > 2:
                    result_data['team_name'] = cells[2].get_text(strip=True)
                if len(cells) > 3:
                    result_data['division'] = cells[3].get_text(strip=True)
                if len(cells) > 4:
                    points_text = cells[4].get_text(strip=True)
                    try:
                        result_data['points_scored'] = float(points_text)
                    except ValueError:
                        pass

                # Try to determine skipper/crew from table or name
                if 'crew' in result_data.get('sailor_name', '').lower():
                    result_data['role'] = 'crew'
                elif 'skipper' in result_data.get('sailor_name', '').lower():
                    result_data['role'] = 'skipper'

                if result_data['sailor_name'] and result_data['placement']:
                    results.append(result_data)

            except Exception as e:
                logger.warning(f"Error parsing row: {e}")
                continue

        return results

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
            regatta_id=regatta_id,
            division=result_data.get('division')
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
            crew_partner=result_data.get('crew_partner')
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
    def _extract_external_id(url):
        """Extract unique ID from URL"""
        # Example: /regatta/12345 -> "12345"
        match = re.search(r'/regatta/(\d+)', url)
        if match:
            return match.group(1)
        return url.split('/')[-1]

    @staticmethod
    def _extract_placement(text):
        """Extract numeric placement from text like '1st', '2nd', '3'"""
        match = re.search(r'(\d+)', text)
        return int(match.group(1)) if match else None

    @staticmethod
    def _parse_date(date_text):
        """Parse date from various formats"""
        formats = [
            '%m/%d/%Y',
            '%Y-%m-%d',
            '%B %d, %Y',
            '%b %d, %Y',
            '%d %B %Y',
            '%d %b %Y'
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_text.strip(), fmt).date()
            except ValueError:
                continue

        # If all else fails, return today
        logger.warning(f"Could not parse date: {date_text}")
        return datetime.utcnow().date()


def run_scraper(limit=None):
    """
    Convenience function to run the scraper

    Args:
        limit: Max regattas to scrape (useful for testing)
    """
    scraper = ClubspotScraper()
    return scraper.scrape_all_regattas(limit=limit)
