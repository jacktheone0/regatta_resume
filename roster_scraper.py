import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
from typing import List, Dict
import sys

def scrape_roster(school_slug: str, season_code: str, school_name: str, source_type='hs') -> List[Dict]:
    """
    Scrape roster for a specific school and season

    Args:
        school_slug: URL-formatted school name (e.g., "southwestern-central")
        season_code: Season code (e.g., "f25", "s24")
        school_name: Full school name for reference
        source_type: 'hs' for high school or 'college' for college

    Returns:
        List of sailor dictionaries
    """
    if source_type == 'college':
        base_url = "https://scores.collegesailing.org"
    else:
        base_url = "https://scores.hssailing.org"

    url = f"{base_url}/schools/{school_slug}/{season_code}/roster/"

    try:
        resp = requests.get(url, timeout=30)

        # If we get a 404, the roster doesn't exist for this season
        if resp.status_code == 404:
            return []

        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        sailors = []

        # Look for sailor names in the roster
        # Common patterns: tables with sailor data, lists of links to sailor pages

        # Method 1: Find links to sailor pages
        sailor_links = soup.find_all("a", href=lambda x: x and "/sailors/" in x)

        for link in sailor_links:
            sailor_name = link.get_text(strip=True)

            if sailor_name and len(sailor_name) > 1:  # Basic validation
                sailors.append({
                    "Sailor_Name": sailor_name,
                    "School": school_name,
                    "Season_Code": season_code,
                    "Year": season_code[1:],  # Extract year (e.g., "25" from "f25")
                    "Season": "Fall" if season_code[0].lower() == "f" else "Spring",
                    "URL": url
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
                        # Look for patterns that indicate a name (contains space, title case)
                        if " " in text and len(text.split()) >= 2:
                            sailors.append({
                                "Sailor_Name": text,
                                "School": school_name,
                                "Season_Code": season_code,
                                "Year": season_code[1:],
                                "Season": "Fall" if season_code[0].lower() == "f" else "Spring",
                                "URL": url
                            })

        # Remove duplicates based on sailor name
        unique_sailors = []
        seen_names = set()
        for sailor in sailors:
            if sailor["Sailor_Name"] not in seen_names:
                unique_sailors.append(sailor)
                seen_names.add(sailor["Sailor_Name"])

        return unique_sailors

    except requests.exceptions.RequestException as e:
        # Silent failure for 404s and connection errors
        return []
    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return []

def scrape_all_rosters(schools_df: pd.DataFrame, seasons: List[str] = None, source_type='hs') -> pd.DataFrame:
    """
    Scrape rosters for all schools across multiple seasons

    Args:
        schools_df: DataFrame with school information (must have 'School_Name' and either 'URL_Slug' or 'Normalized_Slug')
        seasons: List of season codes to scrape (default: f25, s25, f24, s24, f23, s23, f22, s22)
        source_type: 'hs' for high school or 'college' for college

    Returns:
        DataFrame with all sailor rosters
    """
    if seasons is None:
        seasons = ["f25", "s25", "f24", "s24", "f23", "s23", "f22", "s22"]

    all_sailors = []
    total_schools = len(schools_df)
    total_combinations = total_schools * len(seasons)
    current = 0

    print(f"Scraping rosters for {total_schools} schools across {len(seasons)} seasons...")
    print(f"Total combinations to check: {total_combinations}")
    print("-" * 60)

    for idx, row in schools_df.iterrows():
        school_name = row["School_Name"]

        # Use URL_Slug if available, otherwise use Normalized_Slug
        if "URL_Slug" in row and pd.notna(row["URL_Slug"]) and row["URL_Slug"]:
            school_slug = row["URL_Slug"]
        elif "Normalized_Slug" in row and pd.notna(row["Normalized_Slug"]):
            school_slug = row["Normalized_Slug"]
        else:
            print(f"Skipping {school_name}: No URL slug available")
            current += len(seasons)
            continue

        school_sailors_count = 0

        for season in seasons:
            current += 1

            # Progress indicator every 50 requests
            if current % 50 == 0:
                print(f"Progress: {current}/{total_combinations} ({current/total_combinations*100:.1f}%)")

            sailors = scrape_roster(school_slug, season, school_name, source_type=source_type)

            if sailors:
                all_sailors.extend(sailors)
                school_sailors_count += len(sailors)

            # Be polite to the server
            time.sleep(0.5)

        if school_sailors_count > 0:
            print(f"✓ {school_name}: Found {school_sailors_count} sailors")

    print("-" * 60)
    print(f"Scraping complete! Found {len(all_sailors)} total sailor records")

    if not all_sailors:
        return pd.DataFrame()

    df = pd.DataFrame(all_sailors)

    # Remove exact duplicates
    df = df.drop_duplicates()

    # Sort by school, year, season, and name
    df = df.sort_values(["School", "Year", "Season", "Sailor_Name"]).reset_index(drop=True)

    return df

def load_schools_csv(filepath: str = "schools.csv") -> pd.DataFrame:
    """Load schools from CSV file"""
    try:
        df = pd.read_csv(filepath)
        print(f"Loaded {len(df)} schools from {filepath}")
        return df
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return pd.DataFrame()

if __name__ == "__main__":
    # Load schools
    schools_df = load_schools_csv("schools.csv")

    if schools_df.empty:
        print("Error: No schools loaded. Please run school_scraper.py first.")
        sys.exit(1)

    # Scrape all rosters
    rosters_df = scrape_all_rosters(schools_df)

    if not rosters_df.empty:
        # Save to CSV
        output_file = "rosters.csv"
        rosters_df.to_csv(output_file, index=False)
        print(f"\nRosters saved to {output_file}")
        print(f"\nPreview:")
        print(rosters_df.head(10))
        print(f"\nSummary:")
        print(f"Total sailors: {len(rosters_df)}")
        print(f"Unique sailors: {rosters_df['Sailor_Name'].nunique()}")
        print(f"Schools represented: {rosters_df['School'].nunique()}")
        print(f"\nSailors by season:")
        print(rosters_df.groupby(["Year", "Season"]).size())
    else:
        print("No rosters were found. Please check the URLs and page structure.")
