"""
Web scraper for theclubspot.com regatta results
UPDATED: Now scrapes real data from theclubspot.com
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from models import db, Sailor, Regatta, Result, ScraperLog
import logging
import re
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ClubspotScraper:
    """Scraper for theclubspot.com regatta results"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.stats = {
            'regattas_scraped': 0,
            'sailors_added': 0,
            'results_added': 0
        }
        # Known regatta IDs from 2024 (starter list - will expand)
        self.regatta_ids = [
            'r7bw6wf1a8',  # 2024 C420 US NATIONAL CHAMPIONSHIP
            'QyzP8g1eKs',  # 2024 ILCA North American Championship
            'YzyHD2J2y0',  # 2024 US Olympic Team Trials
            'QpjAg9jjdC',  # 2024 J/24 National Championship
            '60QaLUbh0H',  # 2024 U.S. Youth Championship
            'IMIGSj06De',  # 2024 J/24 World Championship
            'pEFUbQJiBg',  # 2024 ILCA USA Masters Regatta
        ]

    def scrape_all_regattas(self, limit=None):
        """
        Main entry point: scrape all available regattas

        Args:
            limit: Maximum number of regattas to scrape
        """
        log = ScraperLog(status='running')
        db.session.add(log)
        db.session.commit()

        try:
            logger.info("Starting scraper...")

            # Use known regatta IDs for now
            regatta_ids = self.regatta_ids[:limit] if limit else self.regatta_ids

            logger.info(f"Found {len(regatta_ids)} regattas to scrape")

            for idx, regatta_id in enumerate(regatta_ids, 1):
                logger.info(f"[{idx}/{len(regatta_ids)}] Scraping regatta: {regatta_id}")
                try:
                    self._scrape_regatta(regatta_id)
                    self.stats['regattas_scraped'] += 1
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

    def _scrape_regatta(self, regatta_id):
        """
        Scrape a single regatta by ID

        Args:
            regatta_id: The clubspot regatta ID
        """
        url = f"https://theclubspot.com/regatta/{regatta_id}"

        try:
            # Fetch the regatta page
            response = self.session.get(url, timeout=30)
            if response.status_code != 200:
                logger.warning(f"Failed to fetch {url}: {response.status_code}")
                return

            soup = BeautifulSoup(response.content, 'lxml')

            # Extract regatta metadata
            regatta_data = self._extract_regatta_metadata(soup, regatta_id, url)
            regatta = self._get_or_create_regatta(regatta_data)

            # Now fetch the results page
            results_url = f"{url}/results"
            results_response = self.session.get(results_url, timeout=30)

            if results_response.status_code == 200:
                results_soup = BeautifulSoup(results_response.content, 'lxml')
                results_data = self._extract_results(results_soup, regatta.id)

                # Save results to database
                for result_data in results_data:
                    self._save_result(result_data, regatta.id)

        except Exception as e:
            logger.error(f"Error in _scrape_regatta for {regatta_id}: {e}")
            raise

    def _extract_regatta_metadata(self, soup, regatta_id, url):
        """Extract regatta information from the page"""
        data = {
            'source_url': url,
            'external_id': regatta_id
        }

        # Try to find regatta name
        name_elem = soup.select_one('h2') or soup.select_one('h1')
        if name_elem:
            data['name'] = name_elem.get_text(strip=True)
        else:
            data['name'] = f"Regatta {regatta_id}"

        # Try to find location (look for common patterns)
        location_patterns = [
            soup.select_one('.location'),
            soup.select_one('[class*="location"]'),
            soup.find(string=re.compile(r'Location:', re.I))
        ]

        for elem in location_patterns:
            if elem:
                location_text = elem.get_text(strip=True) if hasattr(elem, 'get_text') else str(elem)
                data['location'] = location_text.replace('Location:', '').strip()
                break

        # Try to find dates
        date_elem = soup.select_one('.date') or soup.select_one('[class*="date"]')
        if date_elem:
            date_text = date_elem.get_text(strip=True)
            data['start_date'] = self._parse_date(date_text)
        else:
            # Fallback to current date if we can't find it
            data['start_date'] = datetime.utcnow().date()

        # Try to find fleet type
        fleet_elem = soup.select_one('.fleet') or soup.select_one('[class*="class"]')
        if fleet_elem:
            data['fleet_type'] = fleet_elem.get_text(strip=True)

        return data

    def _extract_results(self, soup, regatta_id):
        """
        Extract results from the results page

        Note: This is simplified since the page uses JavaScript.
        For better results, we'd need to use Selenium or requests-html
        to render JavaScript first.
        """
        results = []

        # Look for table with results
        # The page might have multiple possible table structures
        tables = soup.find_all('table')

        for table in tables:
            rows = table.select('tbody tr') if table.select('tbody') else table.select('tr')

            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 2:
                    continue

                try:
                    # Try to extract data from cells
                    # Common patterns: [Place, Sail#, Boat, Skipper/Crew, Points]

                    # Look for placement (usually first column)
                    placement_text = cells[0].get_text(strip=True)
                    placement = self._extract_placement(placement_text)

                    if not placement:
                        continue

                    # Try to find sailor name (usually in middle columns)
                    sailor_name = None
                    for cell in cells[1:4]:  # Check next few columns
                        text = cell.get_text(strip=True)
                        if text and len(text) > 2 and not text.isdigit():
                            # Split on newlines or <br> tags
                            names = text.split('\n')
                            sailor_name = names[0].strip()
                            break

                    if not sailor_name:
                        continue

                    result_data = {
                        'placement': placement,
                        'sailor_name': sailor_name
                    }

                    # Try to extract additional data
                    if len(cells) > 3:
                        # Look for points
                        for cell in cells[-3:]:
                            text = cell.get_text(strip=True)
                            try:
                                points = float(text)
                                result_data['points_scored'] = points
                                break
                            except ValueError:
                                continue

                    results.append(result_data)

                except Exception as e:
                    logger.warning(f"Error parsing row: {e}")
                    continue

        logger.info(f"Extracted {len(results)} results from regatta {regatta_id}")
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
        limit: Max regattas to scrape
    """
    scraper = ClubspotScraper()
    return scraper.scrape_all_regattas(limit=limit)
