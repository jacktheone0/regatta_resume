"""
Schools Page Scraper for HS/College Sailing
Scrapes https://scores.hssailing.org/schools/ and https://scores.collegesailing.org/schools/
to get all sailor names from all schools.
"""
import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import time
import logging
from models import db, SailorName, HSResult, CollegeResult
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SchoolsScraper:
    """Scraper to get all sailor names from schools pages"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.stats = {
            'schools_scraped': 0,
            'sailors_found': 0,
            'results_added': 0
        }

    def scrape_all_schools(self, base_url, source_type='hs', limit_schools=None):
        """
        Scrape all schools to get sailor names

        Args:
            base_url: 'https://scores.hssailing.org' or 'https://scores.collegesailing.org'
            source_type: 'hs' or 'college'
            limit_schools: Max number of schools to scrape (None = all)

        Returns:
            List of sailor names
        """
        schools_url = f"{base_url}/schools/"
        logger.info(f"Fetching schools from {schools_url}")

        try:
            # Get list of all schools
            schools = self._fetch_school_list(schools_url, base_url)

            if limit_schools:
                schools = schools[:limit_schools]

            logger.info(f"Found {len(schools)} schools to scrape")

            all_sailors = []

            for idx, school_info in enumerate(schools, 1):
                logger.info(f"[{idx}/{len(schools)}] Scraping: {school_info['name']}")

                try:
                    sailors = self._scrape_school_sailors(school_info['url'], school_info['name'])
                    all_sailors.extend(sailors)
                    self.stats['schools_scraped'] += 1

                    # Store sailor names in database
                    for sailor_name in sailors:
                        self._store_sailor_name(sailor_name, school_info['name'], source_type)
                        self.stats['sailors_found'] += 1

                    time.sleep(1)  # Be nice to the server

                except Exception as e:
                    logger.error(f"Error scraping school {school_info['name']}: {e}")
                    continue

            logger.info(f"Scraping complete! Found {len(all_sailors)} sailors from {self.stats['schools_scraped']} schools")
            return all_sailors

        except Exception as e:
            logger.error(f"Error scraping schools: {e}")
            raise

    def _fetch_school_list(self, schools_url, base_url):
        """Fetch list of all schools from schools page"""
        response = self.session.get(schools_url, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        schools = []

        # Find all school links
        # Usually in format: /schools/school-name/
        for link in soup.find_all('a', href=True):
            href = link['href']

            if '/schools/' in href and href.count('/') >= 3 and not href.endswith('/schools/'):
                school_name = link.get_text(strip=True)
                if school_name and len(school_name) > 2:
                    full_url = href if href.startswith('http') else base_url + href

                    schools.append({
                        'name': school_name,
                        'url': full_url
                    })

        # Remove duplicates
        seen = set()
        unique = []
        for s in schools:
            if s['url'] not in seen:
                seen.add(s['url'])
                unique.append(s)

        return unique

    def _scrape_school_sailors(self, school_url, school_name):
        """Scrape all sailor names from a school's roster page"""
        try:
            # Try the /sailors/ subpage first
            sailors_url = school_url.rstrip('/') + '/sailors/'
            response = self.session.get(sailors_url, timeout=30)

            if response.status_code != 200:
                # Fallback to main school page
                response = self.session.get(school_url, timeout=30)
                response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            sailors = []

            # Look for sailor links (usually /sailors/sailor-name/)
            for link in soup.find_all('a', href=True):
                href = link['href']

                if '/sailors/' in href and not href.endswith('/sailors/'):
                    sailor_name = link.get_text(strip=True)

                    # Filter out non-name links
                    if sailor_name and len(sailor_name) > 3 and not any(x in sailor_name.lower() for x in ['all sailors', 'roster', 'team']):
                        sailors.append(sailor_name)

            # Remove duplicates
            sailors = list(set(sailors))
            logger.info(f"  Found {len(sailors)} sailors from {school_name}")

            return sailors

        except Exception as e:
            logger.error(f"Error scraping sailors from {school_url}: {e}")
            return []

    def _store_sailor_name(self, name, school, source_type):
        """Store sailor name in SailorName table"""
        return _store_sailor_name(name, school, source_type)


def _store_sailor_name(name, school, source_type):
    """
    Store sailor name in SailorName table

    Args:
        name: Sailor's full name
        school: School name
        source_type: 'hs', 'college', or 'both'

    Returns:
        SailorName object
    """
    name_normalized = name.lower().strip()

    # Check if already exists
    sailor = SailorName.query.filter_by(name_normalized=name_normalized).first()

    if sailor:
        # Update source if needed
        if sailor.source != 'both':
            if (sailor.source == 'hs' and source_type == 'college') or \
               (sailor.source == 'college' and source_type == 'hs'):
                sailor.source = 'both'

        # Update school if not set
        if not sailor.school:
            sailor.school = school

        db.session.commit()
    else:
        # Create new sailor name record
        sailor = SailorName(
            name=name.strip(),
            name_normalized=name_normalized,
            school=school,
            source=source_type
        )
        db.session.add(sailor)
        db.session.commit()

    return sailor


def build_sailor_url(sailor_name: str, base_url: str) -> str:
    """Build sailor profile URL from name"""
    cleaned = sailor_name.strip().lower().replace(" ", "-")
    return f"{base_url}/sailors/{cleaned}/"


def scrape_regattas_from_page(url: str) -> pd.DataFrame:
    """
    Scrape regatta results from a sailor's page
    (User's original scraper code)
    """
    resp = requests.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table", class_="participation-table")
    records = []

    for table in tables:
        tbody = table.find("tbody")
        if not tbody:
            continue
        rows = tbody.find_all("tr", class_=["row0", "row1"])
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            regatta_td = cells[0]
            regatta_link_elem = regatta_td.find("a")
            regatta_name = regatta_link_elem.get_text(strip=True) if regatta_link_elem else regatta_td.get_text(strip=True)
            regatta_link = regatta_link_elem.get('href') if regatta_link_elem else None

            place_td = cells[-1]
            span = place_td.find("span", class_="sailor-placement-container")
            place_text = span.find("a").get_text(strip=True) if span and span.find("a") else place_td.get_text(strip=True)

            date_td = cells[-3]
            span = date_td.find("span", class_="sailor-placement-container")
            date_text = span.find("a").get_text(strip=True) if span and span.find("a") else date_td.get_text(strip=True)

            # Try to extract position (Skipper/Crew) and division from cells
            position = None
            division = None

            for cell in cells[1:-3]:  # Check middle cells
                text = cell.get_text(strip=True)
                if 'Skipper' in text:
                    position = 'Skipper'
                elif 'Crew' in text:
                    position = 'Crew'

                # Division pattern: "A Div", "B Div", etc.
                div_match = re.search(r'([A-Z]\s*Div)', text, re.IGNORECASE)
                if div_match:
                    division = div_match.group(1)

            records.append({
                "Regatta": regatta_name,
                "Regatta_Link": regatta_link,
                "Result": place_text,
                "Position": position,
                "Division": division,
                "Date": date_text
            })

    return pd.DataFrame(records)


def scrape_sailor_results(sailor_name, source_type='hs'):
    """
    Scrape results for a single sailor and store in database

    Args:
        sailor_name: SailorName object
        source_type: 'hs' or 'college'
    """
    base_url = 'https://scores.hssailing.org' if source_type == 'hs' else 'https://scores.collegesailing.org'
    url = build_sailor_url(sailor_name.name, base_url)

    logger.info(f"Scraping {source_type.upper()} results for {sailor_name.name}")

    try:
        df = scrape_regattas_from_page(url)

        if df.empty:
            logger.info(f"  No results found for {sailor_name.name}")
            return 0

        # Parse and store each result
        results_added = 0

        for _, row in df.iterrows():
            try:
                # Extract place and total from "21/32" format
                place_match = re.match(r'(\d+)/(\d+)', row['Result'])
                if not place_match:
                    continue

                place_numeric = int(place_match.group(1))
                total_boats = int(place_match.group(2))

                # Parse date
                regatta_date = None
                if row['Date']:
                    try:
                        regatta_date = datetime.strptime(row['Date'], '%m/%d/%Y').date()
                    except:
                        pass

                # Build raw row data
                raw_row = f"{row['Regatta']} | {row['Result']} | {row.get('Position', '')} | {row.get('Division', '')} | {row['Date']}"

                # Create result record
                result_data = {
                    'sailor_name_id': sailor_name.id,
                    'regatta_name': row['Regatta'],
                    'regatta_link': row.get('Regatta_Link'),
                    'regatta_date': regatta_date,
                    'place': row['Result'],
                    'place_numeric': place_numeric,
                    'total_boats': total_boats,
                    'position': row.get('Position'),
                    'division': row.get('Division'),
                    'school': sailor_name.school,
                    'raw_row_data': raw_row
                }

                # Store in appropriate table
                if source_type == 'hs':
                    # Check if already exists
                    existing = HSResult.query.filter_by(
                        sailor_name_id=sailor_name.id,
                        regatta_name=row['Regatta']
                    ).first()

                    if not existing:
                        result = HSResult(**result_data)
                        db.session.add(result)
                        results_added += 1
                else:
                    existing = CollegeResult.query.filter_by(
                        sailor_name_id=sailor_name.id,
                        regatta_name=row['Regatta']
                    ).first()

                    if not existing:
                        result = CollegeResult(**result_data)
                        db.session.add(result)
                        results_added += 1

            except Exception as e:
                logger.error(f"Error storing result: {e}")
                continue

        db.session.commit()
        logger.info(f"  Added {results_added} results for {sailor_name.name}")
        return results_added

    except Exception as e:
        logger.error(f"Error scraping results for {sailor_name.name}: {e}")
        return 0


def run_full_hs_scraper(limit_schools=None, limit_sailors=None):
    """
    Full HS scraper using proven 3-step approach:
    Step 1: Get all schools
    Step 2: Get all rosters (school + season combinations)
    Step 3: Scrape individual sailor results

    Args:
        limit_schools: Max schools to scrape (None = all)
        limit_sailors: Max sailors to scrape results for (None = all)
    """
    from app import app
    from schools_scraper_core import scrape_schools, scrape_all_rosters

    with app.app_context():
        base_url = 'https://scores.hssailing.org'

        # Step 1: Scrape all schools
        logger.info("=== STEP 1: Scraping schools ===")
        schools = scrape_schools(base_url)

        if limit_schools:
            schools = schools[:limit_schools]
            logger.info(f"Limited to {limit_schools} schools")

        logger.info(f"Found {len(schools)} schools")

        # Step 2: Scrape all rosters to get sailor names
        logger.info("=== STEP 2: Scraping rosters for all schools ===")
        seasons = ["f25", "s25", "f24", "s24", "f23", "s23", "f22", "s22"]
        all_sailors = scrape_all_rosters(base_url, schools, seasons)

        logger.info(f"Found {len(all_sailors)} total sailor records from rosters")

        # Store sailors in database
        logger.info("Storing sailor names in database...")
        sailors_added = 0
        for sailor_data in all_sailors:
            sailor = _store_sailor_name(
                sailor_data['sailor_name'],
                sailor_data['school'],
                'hs'
            )
            sailors_added += 1

        logger.info(f"Stored {sailors_added} sailor names")

        # Step 3: Scrape results for each sailor
        logger.info("=== STEP 3: Scraping individual results for each sailor ===")

        hs_sailors = SailorName.query.filter(
            SailorName.source.in_(['hs', 'both'])
        ).all()

        if limit_sailors:
            hs_sailors = hs_sailors[:limit_sailors]

        logger.info(f"Found {len(hs_sailors)} HS sailors to scrape")

        total_results = 0
        for idx, sailor in enumerate(hs_sailors, 1):
            logger.info(f"[{idx}/{len(hs_sailors)}] Processing {sailor.name}")
            results = scrape_sailor_results(sailor, source_type='hs')
            total_results += results
            time.sleep(0.5)  # Be nice to the server

        logger.info(f"=== COMPLETE: Added {total_results} results for {len(hs_sailors)} sailors ===")


def run_full_college_scraper(limit_schools=None, limit_sailors=None):
    """
    Full College scraper using proven 3-step approach:
    Step 1: Get all schools
    Step 2: Get all rosters (school + season combinations)
    Step 3: Scrape individual sailor results

    Args:
        limit_schools: Max schools to scrape (None = all)
        limit_sailors: Max sailors to scrape results for (None = all)
    """
    from app import app
    from schools_scraper_core import scrape_schools, scrape_all_rosters

    with app.app_context():
        base_url = 'https://scores.collegesailing.org'

        # Step 1: Scrape all schools
        logger.info("=== STEP 1: Scraping schools ===")
        schools = scrape_schools(base_url)

        if limit_schools:
            schools = schools[:limit_schools]
            logger.info(f"Limited to {limit_schools} schools")

        logger.info(f"Found {len(schools)} schools")

        # Step 2: Scrape all rosters to get sailor names
        logger.info("=== STEP 2: Scraping rosters for all schools ===")
        seasons = ["f25", "s25", "f24", "s24", "f23", "s23", "f22", "s22"]
        all_sailors = scrape_all_rosters(base_url, schools, seasons)

        logger.info(f"Found {len(all_sailors)} total sailor records from rosters")

        # Store sailors in database
        logger.info("Storing sailor names in database...")
        sailors_added = 0
        for sailor_data in all_sailors:
            sailor = _store_sailor_name(
                sailor_data['sailor_name'],
                sailor_data['school'],
                'college'
            )
            sailors_added += 1

        logger.info(f"Stored {sailors_added} sailor names")

        # Step 3: Scrape results for each sailor
        logger.info("=== STEP 3: Scraping individual results for each sailor ===")

        college_sailors = SailorName.query.filter(
            SailorName.source.in_(['college', 'both'])
        ).all()

        if limit_sailors:
            college_sailors = college_sailors[:limit_sailors]

        logger.info(f"Found {len(college_sailors)} College sailors to scrape")

        total_results = 0
        for idx, sailor in enumerate(college_sailors, 1):
            logger.info(f"[{idx}/{len(college_sailors)}] Processing {sailor.name}")
            results = scrape_sailor_results(sailor, source_type='college')
            total_results += results
            time.sleep(0.5)  # Be nice to the server

        logger.info(f"=== COMPLETE: Added {total_results} results for {len(college_sailors)} sailors ===")


if __name__ == '__main__':
    # Test with limited scope
    run_full_hs_scraper(limit_schools=2, limit_sailors=10)
