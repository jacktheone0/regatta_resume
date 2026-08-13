import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
import time
from typing import List, Dict, Optional
import sys

def build_sailor_url(sailor_name: str, base_url: str) -> str:
    cleaned = sailor_name.strip().lower().replace(" ", "-")
    return f"{base_url}{cleaned}/"

def _cell_text(td) -> str:
    """Cell text, preferring the linked value inside placement containers"""
    span = td.find("span", class_="sailor-placement-container")
    if span and span.find("a"):
        return span.find("a").get_text(strip=True)
    a = td.find("a")
    if a:
        return a.get_text(strip=True)
    return td.get_text(strip=True)

def _header_columns(table) -> Dict[str, int]:
    """Map lowercased header names to column indices for one table"""
    header_row = None
    thead = table.find("thead")
    if thead:
        header_row = thead.find("tr")
    if header_row is None:
        first = table.find("tr")
        if first and first.find("th"):
            header_row = first
    if header_row is None:
        return {}
    return {cell.get_text(strip=True).lower(): idx
            for idx, cell in enumerate(header_row.find_all(["th", "td"]))}

def _find_col(headers: Dict[str, int], *wanted) -> Optional[int]:
    for name, idx in headers.items():
        for want in wanted:
            if want in name:
                return idx
    return None

def _normalize_position(text: str):
    """
    'Skipper', 'Crew', 'Skipper (A)' etc. -> (position, embedded division).
    The sailor page is per-sailor, so the value is the scraped sailor's own.
    """
    if not text:
        return None, None
    stripped = text.strip()
    low = stripped.lower()
    if low.startswith("skipper"):
        position = "Skipper"
    elif low.startswith("crew"):
        position = "Crew"
    else:
        position = stripped[:20]
    match = re.search(r"(?<![A-Za-z])([A-Da-d])(?![A-Za-z])", stripped)
    division = f"{match.group(1).upper()} Div" if match else None
    return position, division

def scrape_regattas_from_page(url: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table", class_="participation-table")
    records = []

    for table in tables:
        # Locate columns by header name; the HS and college sites don't
        # share an exact layout. Fall back to the historical fixed
        # positions when a header is missing.
        headers = _header_columns(table)
        regatta_col = _find_col(headers, "regatta", "name")
        date_col = _find_col(headers, "date")
        position_col = _find_col(headers, "position", "role")
        division_col = _find_col(headers, "division", "div")
        place_col = _find_col(headers, "finish", "place", "result")

        tbody = table.find("tbody")
        if not tbody:
            continue
        rows = tbody.find_all("tr", class_=["row0", "row1"])
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            def cell_at(col, fallback):
                idx = col if col is not None and col < len(cells) else fallback
                if idx is None:
                    return ""
                return _cell_text(cells[idx])

            regatta_name = cell_at(regatta_col, 0)
            place_text = cell_at(place_col, -1)
            date_text = cell_at(date_col, -3)
            position_text = cell_at(position_col, None)
            division_text = cell_at(division_col, None)

            position, embedded_division = _normalize_position(position_text)
            division = division_text or embedded_division

            records.append({
                "Regatta": regatta_name,
                "Result": place_text,
                "Date": date_text,
                "Position": position,
                "Division": division
            })

    return pd.DataFrame(records, columns=["Regatta", "Result", "Date", "Position", "Division"])

def scrape_all_sites(name: str) -> pd.DataFrame:
    cleaned_name = name.replace(" ", "-").lower()
    sites = [
        ("HS", f"https://scores.hssailing.org/sailors/{cleaned_name}/"),
        ("College", f"https://scores.collegesailing.org/sailors/{cleaned_name}/")
    ]
    all_results = []
    for label, url in sites:
        try:
            df = scrape_regattas_from_page(url)
            if not df.empty:
                df["Source"] = label
                all_results.append(df)
        except:
            continue

    if not all_results:
        return pd.DataFrame()
    return pd.concat(all_results, ignore_index=True)

def expand_result_fields(df):
    df[["Place", "Total"]] = df["Result"].str.extract(r'(\d+)/(\d+)', expand=True)
    return df

def scrape_sailor_with_metadata(sailor_name: str, school: str = None, season: str = None, year: str = None) -> Dict:
    """
    Scrape a sailor's results and include metadata

    Returns:
        Dictionary with sailor info and results DataFrame
    """
    results_df = scrape_all_sites(sailor_name)

    return {
        "sailor_name": sailor_name,
        "school": school,
        "season": season,
        "year": year,
        "results_df": results_df,
        "num_regattas": len(results_df) if not results_df.empty else 0
    }

def scrape_batch_sailors(sailors_df: pd.DataFrame, delay: float = 0.5, save_interval: int = 50) -> pd.DataFrame:
    """
    Scrape results for a batch of sailors from a DataFrame

    Args:
        sailors_df: DataFrame with at least 'Sailor_Name' column
        delay: Seconds to wait between requests (be polite to server)
        save_interval: Save progress every N sailors

    Returns:
        DataFrame with all results
    """
    all_results = []
    total_sailors = len(sailors_df)

    print(f"Starting batch scrape for {total_sailors} sailors...")
    print("-" * 60)

    for idx, row in sailors_df.iterrows():
        sailor_name = row["Sailor_Name"]
        school = row.get("School", None)
        season = row.get("Season", None)
        year = row.get("Year", None)

        try:
            sailor_data = scrape_sailor_with_metadata(sailor_name, school, season, year)

            if not sailor_data["results_df"].empty:
                # Add metadata to results
                results = sailor_data["results_df"].copy()
                results["Sailor_Name"] = sailor_name
                results["School"] = school
                results["Roster_Season"] = season
                results["Roster_Year"] = year

                all_results.append(results)

                print(f"✓ [{idx+1}/{total_sailors}] {sailor_name}: {sailor_data['num_regattas']} regattas")
            else:
                print(f"- [{idx+1}/{total_sailors}] {sailor_name}: No results found")

           

        except Exception as e:
            print(f"✗ [{idx+1}/{total_sailors}] {sailor_name}: Error - {e}")

        # Be polite to the server
        time.sleep(delay)

    print("-" * 60)

    if not all_results:
        print("No results found for any sailors.")
        return pd.DataFrame()

    final_df = pd.concat(all_results, ignore_index=True)
    final_df = expand_result_fields(final_df)

    print(f"Scraping complete! Found {len(final_df)} total regatta results")

    return final_df

def load_rosters_csv(filepath: str = "rosters.csv") -> pd.DataFrame:
    """Load rosters from CSV file"""
    try:
        df = pd.read_csv(filepath)
        print(f"Loaded {len(df)} sailor records from {filepath}")

        # Remove duplicates - keep first occurrence of each sailor
        unique_df = df.drop_duplicates(subset=["Sailor_Name"], keep="first")

        if len(unique_df) < len(df):
            print(f"Removed {len(df) - len(unique_df)} duplicate sailor entries")
            print(f"Unique sailors to scrape: {len(unique_df)}")

        return unique_df
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return pd.DataFrame()

if __name__ == "__main__":
    # Load rosters
    rosters_df = load_rosters_csv("rosters.csv")

    if rosters_df.empty:
        print("Error: No rosters loaded. Please run roster_scraper.py first.")
        sys.exit(1)

    # Scrape all sailors
    results_df = scrape_batch_sailors(rosters_df, delay=0.5, save_interval=50)

    if not results_df.empty:
        # Save final results
        output_file = "all_sailor_results.csv"
        results_df.to_csv(output_file, index=False)
        print(f"\nAll results saved to {output_file}")

        print(f"\nFinal Summary:")
        print(f"Total regatta results: {len(results_df)}")
        print(f"Unique sailors with results: {results_df['Sailor_Name'].nunique()}")
        print(f"Unique regattas: {results_df['Regatta'].nunique()}")
        print(f"\nResults by source:")
        print(results_df["Source"].value_counts())

        # Show some statistics
        if "Place" in results_df.columns:
            results_df["Place"] = pd.to_numeric(results_df["Place"], errors="coerce")
            print(f"\nPlacement statistics:")
            print(results_df["Place"].describe())
    else:
        print("No results were found.")
