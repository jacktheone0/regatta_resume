import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
from typing import List, Dict, Optional
import sys

def build_sailor_url(sailor_name: str, base_url: str) -> str:
    cleaned = sailor_name.strip().lower().replace(" ", "-")
    return f"{base_url}{cleaned}/"

def scrape_regattas_from_page(url: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=30)
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
            regatta_name = regatta_td.find("a").get_text(strip=True) if regatta_td.find("a") else regatta_td.get_text(strip=True)

            place_td = cells[-1]
            span = place_td.find("span", class_="sailor-placement-container")
            place_text = span.find("a").get_text(strip=True) if span and span.find("a") else place_td.get_text(strip=True)

            date_td = cells[-3]
            span = date_td.find("span", class_="sailor-placement-container")
            date_text = span.find("a").get_text(strip=True) if span and span.find("a") else date_td.get_text(strip=True)

            records.append({
                "Regatta": regatta_name,
                "Result": place_text,
                "Date": date_text
            })

    return pd.DataFrame(records, columns=["Regatta", "Result", "Date"])

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

            # Save progress periodically
            if (idx + 1) % save_interval == 0:
                progress_df = pd.concat(all_results, ignore_index=True) if all_results else pd.DataFrame()
                progress_file = f"results_progress_{idx+1}.csv"
                progress_df.to_csv(progress_file, index=False)
                print(f"  → Progress saved to {progress_file}")

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
