import requests
from bs4 import BeautifulSoup
import pandas as pd

def build_sailor_url(sailor_name: str, base_url: str) -> str:
    cleaned = sailor_name.strip().lower().replace(" ", "-")
    return f"{base_url}{cleaned}/"

def scrape_regattas_from_page(url: str) -> pd.DataFrame:
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