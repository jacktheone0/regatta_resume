"""
School Scraper for HS/College Sailing
Adapted from user's proven BeautifulSoup-based approach
Scrapes https://scores.hssailing.org/schools/ and https://scores.collegesailing.org/schools/
"""
import requests
from bs4 import BeautifulSoup
import time
from typing import List, Dict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def scrape_schools(base_url: str = "https://scores.hssailing.org") -> List[Dict]:
    """
    Scrape all schools from /schools/ page

    Args:
        base_url: Base URL (hssailing.org or collegesailing.org)

    Returns:
        List of school dictionaries with name, district, URL slug
    """
    url = f"{base_url}/schools/"
    logger.info(f"Scraping schools from {url}...")

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        schools_data = []

        # Find all district sections (districts are usually in h3 tags)
        districts = soup.find_all("h3")

        if not districts:
            # Fallback: look for any organizational structure
            districts = soup.find_all(["h2", "h3", "h4"])

        for district_header in districts:
            district_name = district_header.get_text(strip=True)

            # Find the next sibling that contains schools (usually a ul or div)
            schools_container = district_header.find_next_sibling()

            if schools_container:
                # Look for all links in this container
                school_links = schools_container.find_all("a")

                for link in school_links:
                    school_name = link.get_text(strip=True)
                    school_url = link.get("href", "")

                    # Extract the URL slug
                    if school_url:
                        url_slug = school_url.rstrip("/").split("/")[-1]
                    else:
                        url_slug = ""

                    schools_data.append({
                        "district": district_name,
                        "school_name": school_name,
                        "url_slug": url_slug,
                        "full_url": school_url if school_url.startswith("http") else f"{base_url}{school_url}"
                    })

        # Fallback if above method doesn't work
        if not schools_data:
            logger.info("Using fallback method to find schools...")
            all_links = soup.find_all("a", href=lambda x: x and "/schools/" in x and x != "/schools/")

            for link in all_links:
                school_name = link.get_text(strip=True)
                school_url = link.get("href", "")

                if school_name and school_url:
                    url_slug = school_url.rstrip("/").split("/")[-1]

                    schools_data.append({
                        "district": "Unknown",
                        "school_name": school_name,
                        "url_slug": url_slug,
                        "full_url": school_url if school_url.startswith("http") else f"{base_url}{school_url}"
                    })

        # Remove duplicates
        seen_schools = set()
        unique_schools = []
        for school in schools_data:
            if school['school_name'] not in seen_schools:
                unique_schools.append(school)
                seen_schools.add(school['school_name'])

        logger.info(f"Found {len(unique_schools)} schools")
        return unique_schools

    except Exception as e:
        logger.error(f"Error scraping schools: {e}")
        return []


def scrape_roster(base_url: str, school_slug: str, season_code: str, school_name: str) -> List[Dict]:
    """
    Scrape roster for a specific school and season

    Args:
        base_url: Base URL (hssailing.org or collegesailing.org)
        school_slug: URL-formatted school name (e.g., "southwestern-central")
        season_code: Season code (e.g., "f25", "s24")
        school_name: Full school name for reference

    Returns:
        List of sailor dictionaries
    """
    url = f"{base_url}/schools/{school_slug}/{season_code}/roster/"

    try:
        resp = requests.get(url, timeout=30)

        # If we get a 404, the roster doesn't exist for this season
        if resp.status_code == 404:
            return []

        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        sailors = []

        # Method 1: Find links to sailor pages
        sailor_links = soup.find_all("a", href=lambda x: x and "/sailors/" in x)

        for link in sailor_links:
            sailor_name = link.get_text(strip=True)
            sailor_url = link.get("href", "")

            if sailor_name and len(sailor_name) > 1:
                # Build full sailor URL
                if not sailor_url.startswith("http"):
                    sailor_url = base_url + sailor_url

                sailors.append({
                    "sailor_name": sailor_name,
                    "school": school_name,
                    "season_code": season_code,
                    "year": season_code[1:],  # Extract year (e.g., "25" from "f25")
                    "season": "Fall" if season_code[0].lower() == "f" else "Spring",
                    "sailor_url": sailor_url,
                    "roster_url": url
                })

        # Method 2: If no links found, look in tables
        if not sailors:
            tables = soup.find_all("table")
            for table in tables:
                rows = table.find_all("tr")
                for row in rows[1:]:  # Skip header
                    cells = row.find_all(["td", "th"])
                    for cell in cells:
                        text = cell.get_text(strip=True)
                        # Look for patterns that indicate a name
                        if " " in text and len(text.split()) >= 2:
                            sailors.append({
                                "sailor_name": text,
                                "school": school_name,
                                "season_code": season_code,
                                "year": season_code[1:],
                                "season": "Fall" if season_code[0].lower() == "f" else "Spring",
                                "sailor_url": "",
                                "roster_url": url
                            })

        # Remove duplicates
        unique_sailors = []
        seen_names = set()
        for sailor in sailors:
            if sailor["sailor_name"] not in seen_names:
                unique_sailors.append(sailor)
                seen_names.add(sailor["sailor_name"])

        return unique_sailors

    except requests.exceptions.RequestException:
        # Silent failure for 404s and connection errors
        return []
    except Exception as e:
        logger.debug(f"Error scraping {url}: {e}")
        return []


def scrape_all_rosters(base_url: str, schools: List[Dict], seasons: List[str] = None) -> List[Dict]:
    """
    Scrape rosters for all schools across multiple seasons

    Args:
        base_url: Base URL
        schools: List of school dictionaries
        seasons: List of season codes to scrape

    Returns:
        List of all sailor dictionaries
    """
    if seasons is None:
        seasons = ["f25", "s25", "f24", "s24", "f23", "s23", "f22", "s22"]

    all_sailors = []
    total_schools = len(schools)
    total_combinations = total_schools * len(seasons)
    current = 0

    logger.info(f"Scraping rosters for {total_schools} schools across {len(seasons)} seasons...")
    logger.info(f"Total combinations to check: {total_combinations}")

    for school in schools:
        school_name = school["school_name"]
        school_slug = school["url_slug"]

        if not school_slug:
            logger.warning(f"Skipping {school_name}: No URL slug available")
            current += len(seasons)
            continue

        school_sailors_count = 0

        for season in seasons:
            current += 1

            # Progress indicator every 50 requests
            if current % 50 == 0:
                logger.info(f"Progress: {current}/{total_combinations} ({current/total_combinations*100:.1f}%)")

            sailors = scrape_roster(base_url, school_slug, season, school_name)

            if sailors:
                all_sailors.extend(sailors)
                school_sailors_count += len(sailors)

            # Be polite to the server
            time.sleep(0.5)

        if school_sailors_count > 0:
            logger.info(f"✓ {school_name}: Found {school_sailors_count} sailors")

    logger.info(f"Scraping complete! Found {len(all_sailors)} total sailor records")
    return all_sailors
