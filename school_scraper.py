import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
from typing import List, Dict

def scrape_schools(source_type='hs') -> pd.DataFrame:
    """
    Scrape all schools from scores.hssailing.org or scores.collegesailing.org

    Args:
        source_type: 'hs' for high school or 'college' for college

    Returns a DataFrame with school names organized by district/conference
    """
    if source_type == 'college':
        url = "https://scores.collegesailing.org/schools/"
    else:
        url = "https://scores.hssailing.org/schools/"

    print(f"Scraping {source_type.upper()} schools from {url}...")

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        schools_data = []

        # Find all district sections
        # The page typically has headers for districts followed by school lists
        districts = soup.find_all("h3")  # Districts are usually in h3 tags

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
                        # Remove trailing slash and get the last part
                        url_slug = school_url.rstrip("/").split("/")[-1]
                    else:
                        url_slug = ""

                    schools_data.append({
                        "District": district_name,
                        "School_Name": school_name,
                        "URL_Slug": url_slug,
                        "Full_URL": school_url if school_url.startswith("http") else f"https://scores.hssailing.org{school_url}"
                    })

        # If the above method doesn't work, use a more general approach
        if not schools_data:
            print("Using fallback method to find schools...")
            all_links = soup.find_all("a", href=lambda x: x and "/schools/" in x and x != "/schools/")

            for link in all_links:
                school_name = link.get_text(strip=True)
                school_url = link.get("href", "")

                if school_name and school_url:
                    url_slug = school_url.rstrip("/").split("/")[-1]

                    schools_data.append({
                        "District": "Unknown",
                        "School_Name": school_name,
                        "URL_Slug": url_slug,
                        "Full_URL": school_url if school_url.startswith("http") else f"https://scores.hssailing.org{school_url}"
                    })

        df = pd.DataFrame(schools_data)

        if df.empty:
            print("Warning: No schools found. The page structure may have changed.")
            return df

        # Remove duplicates
        df = df.drop_duplicates(subset=["School_Name"])

        # Sort by district and school name
        df = df.sort_values(["District", "School_Name"]).reset_index(drop=True)

        print(f"Found {len(df)} schools across {df['District'].nunique()} districts")

        return df

    except Exception as e:
        print(f"Error scraping schools: {e}")
        return pd.DataFrame()

def normalize_school_name_for_url(school_name: str) -> str:
    """
    Convert school name to URL format:
    - Remove "High School", "School", "High" from the end
    - Replace spaces with hyphens
    - Convert to lowercase

    Examples:
    - "Southwestern Central High School" -> "southwestern-central"
    - "Severn School" -> "severn"
    - "Calvert Hall College High School" -> "calvert-hall-college"
    """
    # Remove common school suffixes
    name = school_name.strip()

    # Remove variations of school suffixes (case insensitive)
    suffixes_to_remove = [
        " High School",
        " high school",
        " School",
        " school",
        " High",
        " high"
    ]

    for suffix in suffixes_to_remove:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()

    # Replace spaces with hyphens and convert to lowercase
    url_slug = name.replace(" ", "-").lower()

    return url_slug

def verify_url_slugs(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add a normalized URL slug based on school name
    This helps verify if the URL slug we have matches what we'd expect
    """
    df["Normalized_Slug"] = df["School_Name"].apply(normalize_school_name_for_url)
    return df

if __name__ == "__main__":
    # Scrape schools
    schools_df = scrape_schools()

    if not schools_df.empty:
        # Add normalized slugs for verification
        schools_df = verify_url_slugs(schools_df)

        # Save to CSV
        output_file = "schools.csv"
        schools_df.to_csv(output_file, index=False)
        print(f"\nSchools saved to {output_file}")
        print(f"\nPreview:")
        print(schools_df.head(10))
        print(f"\nDistrict summary:")
        print(schools_df["District"].value_counts())
    else:
        print("No schools were found. Please check the URL and page structure.")
