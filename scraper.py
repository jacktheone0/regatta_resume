"""
Web scraper for theclubspot.com regatta results
Uses Parse API to fetch all regatta IDs, then scrapes each regatta's results
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
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

        # Parse API configuration for fetching regatta IDs
        self.parse_headers = {
            'Content-Type': 'text/plain',
            'Origin': 'https://theclubspot.com',
            'Referer': 'https://theclubspot.com/events',
            'User-Agent': 'Mozilla/5.0',
        }
        self.parse_api_url = 'https://theclubspot.com/parse/classes/regattas'

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
                regatta_id = regatta_data.get('objectId')
                regatta_name = regatta_data.get('name', 'Unknown')
                logger.info(f"[{idx}/{len(regattas_data)}] Scraping: {regatta_name} ({regatta_id})")
                try:
                    self._scrape_regatta(regatta_id, regatta_data)
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

    def _scrape_regatta(self, regatta_id, api_data=None):
        """
        Scrape a single regatta by ID

        Args:
            regatta_id: The clubspot regatta ID
            api_data: Optional dict with regatta metadata from Parse API
        """
        url = f"https://theclubspot.com/regatta/{regatta_id}"

        try:
            # Create or update regatta record using API data if available
            if api_data:
                regatta_metadata = self._parse_api_regatta_data(api_data, regatta_id, url)
            else:
                # Fallback: scrape the regatta page for metadata
                response = self.session.get(url, timeout=30)
                if response.status_code != 200:
                    logger.warning(f"Failed to fetch {url}: {response.status_code}")
                    return
                soup = BeautifulSoup(response.content, 'lxml')
                regatta_metadata = self._extract_regatta_metadata(soup, regatta_id, url)

            regatta = self._get_or_create_regatta(regatta_metadata)

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

    def _parse_api_regatta_data(self, api_data, regatta_id, url):
        """
        Parse regatta metadata from Parse API response

        Args:
            api_data: Dict from Parse API with regatta data
            regatta_id: The regatta objectId
            url: The regatta URL

        Returns:
            Dict with regatta metadata for database
        """
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


def run_scraper(limit=None, start_year=2024):
    """
    Convenience function to run the scraper

    Args:
        limit: Max regattas to scrape (default: all available)
        start_year: Only scrape regattas from this year onwards (default: 2024)
    """
    scraper = ClubspotScraper()
    return scraper.scrape_all_regattas(limit=limit, start_year=start_year)
