import csv
import argparse
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Optional
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ----------------------------
# CLI args
# ----------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Clubspot regatta results searcher (date-range aware)")
    p.add_argument("--name", default="Christopher Fulton", help="Target sailor name (case-insensitive)")
    p.add_argument("--max", type=int, default=250, help="Max regattas to check (after filtering/sorting)")
    p.add_argument("--contains", default="", help="Only check regattas whose name contains this substring (optional)")
    p.add_argument("--timeout", type=int, default=12, help="Seconds to wait for results tables")
    # New: explicit date window in YYYY-MM-DD (UTC, inclusive)
    p.add_argument("--start_date", default=None, help="Only include regattas with startDate >= this date (YYYY-MM-DD)")
    p.add_argument("--end_date", default=None, help="Only include regattas with startDate <= this date (YYYY-MM-DD)")
    return p.parse_args()

# ----------------------------
# Clubspot API request
# ----------------------------
HEADERS = {
    "Content-Type": "text/plain",
    "Origin": "https://theclubspot.com",
    "Referer": "https://theclubspot.com/events",
    "User-Agent": "Mozilla/5.0",
}

DATA = {
    "where": {
        "archived": {"$ne": True},
        "public": True,
        # Exclusions retained from your earlier snippet
        "clubObject": {"$nin": ["HCyTbbCF4n", "XVgOrNASDY", "ecNpKgrusD", "GTKaJKeque", "TTBnsppUug", "pnBFlwJ2Mf"]},
    },
    "include": "clubObject",
    "keys": "objectId,name,startDate,endDate,clubObject.id,clubObject.name",
    "count": 1,
    "limit": 15000,
    "order": "-startDate",
    "_method": "GET",
    "_ApplicationId": "myclubspot2017",
    "_ClientVersion": "js4.3.1-forked-1.0",
    "_InstallationId": "ce500aaa-c2a0-4d06-a9e3-1a558a606542",
}

def fetch_regattas() -> List[Dict]:
    resp = requests.post("https://theclubspot.com/parse/classes/regattas", headers=HEADERS, json=DATA, timeout=45)
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("results", [])

def parse_iso_date(d: Optional[str]) -> datetime:
    """Parse Clubspot's ISO string ('...Z') into timezone-aware UTC datetime."""
    if not d:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(d.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

def parse_cli_date(d: Optional[str]) -> Optional[datetime]:
    """Parse YYYY-MM-DD into UTC midnight inclusive bound."""
    if not d:
        return None
    # Interpret as UTC midnight
    return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)

# ----------------------------
# Selenium driver tuned for speed
# ----------------------------
def make_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--disable-extensions")
    options.add_argument("--log-level=3")
    options.page_load_strategy = "eager"
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver

# ----------------------------
# Search page for name (table rows only)
# ----------------------------
def page_has_name(driver: webdriver.Chrome, target: str, timeout: int) -> Tuple[bool, str]:
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))
    except TimeoutException:
        return False, "timeout_waiting_for_table"

    target_low = target.lower()
    tables = driver.find_elements(By.CSS_SELECTOR, "table")
    saw_rows = False

    for t in tables:
        try:
            rows = t.find_elements(By.CSS_SELECTOR, "tbody tr")
            if rows:
                saw_rows = True
            for row in rows:
                row_text = row.text.strip().lower()
                if row_text and target_low in row_text:
                    return True, "matched_in_table_row"
        except Exception:
            continue

    if not saw_rows:
        return False, "no_rows_found"
    return False, "name_not_found_in_rows"

# ----------------------------
# Main
# ----------------------------
def main():
    args = parse_args()
    target_name = args.name.strip()
    contains_filter = args.contains.strip().lower()

    # New: date window (inclusive, UTC)
    start_bound = parse_cli_date(args.start_date)  # include if start >= this
    end_bound = parse_cli_date(args.end_date)  # include if start <= this

    print("Pulling regattas list...")
    all_regs = fetch_regattas()
    print(f"Fetched {len(all_regs)} total regattas from API.")

    # Build filtered list using explicit date window + name filter
    cleaned = []
    seen_ids = set()

    for r in all_regs:
        rid = r.get("objectId")
        if not rid or rid in seen_ids:
            continue
        seen_ids.add(rid)

        name = r.get("name") or "Unnamed Regatta"
        club = (r.get("clubObject") or {}).get("name", "")
        start_iso = (r.get("startDate") or {}).get("iso")
        end_iso = (r.get("endDate") or {}).get("iso")
        start = parse_iso_date(start_iso)
        end = parse_iso_date(end_iso)

        # Apply date window on start date (inclusive)
        if start_bound and start < start_bound:
            continue
        if end_bound and start > end_bound:
            continue
        if contains_filter and contains_filter not in name.lower():
            continue

        cleaned.append(
            {
                "objectId": rid,
                "name": name,
                "club": club,
                "start": start,
                "end": end,
            }
        )

    # Newest → oldest; then cap
    cleaned.sort(key=lambda x: x["start"], reverse=True)
    regattas = cleaned[: args.max]

    window_desc = []
    if start_bound:
        window_desc.append(f">= {start_bound.date()}")
    if end_bound:
        window_desc.append(f"<= {end_bound.date()}")
    window_text = " and ".join(window_desc) if window_desc else "all dates"
    extra = f" (filtered by name contains '{args.contains}')" if args.contains else ""

    print(f"Will check {len(regattas)} regattas in window [{window_text}]{extra}.")

    driver = make_driver()
    searched_rows = []  # everything checked
    matches = []  # hits only

    try:
        for idx, r in enumerate(regattas, 1):
            rid = r["objectId"]
            url = f"https://theclubspot.com/regatta/{rid}/results"
            name = r["name"]
            club = r["club"] or ""
            start_str = r["start"].strftime("%Y-%m-%d")

            print(f"[{idx:03}/{len(regattas)}] Checking: {name} | {club} | {start_str}")
            try:
                driver.get(url)
            except (TimeoutException, WebDriverException):
                status = "page_load_error"
                searched_rows.append([idx, name, club, start_str, url, status])
                print(f" ⚠️ {status}")
                continue

            found, detail = page_has_name(driver, target_name, timeout=args.timeout)
            status = "FOUND" if found else detail
            searched_rows.append([idx, name, club, start_str, url, status])

            if found:
                matches.append([name, club, start_str, url])
                print(f" ✅ Found '{target_name}'")
            else:
                print(f" … {status}")
    finally:
        driver.quit()

    # --- NEW: Log all sailor names being checked ---
    with open("checked_sailors_log.txt", "w", encoding="utf-8") as log_file:
        log_file.write("Sailors being checked:\n")
        for row in searched_rows:
            # Assuming row[5] contains the sailor names being checked
            sailor_name = row[5] if len(row) > 5 else "Unknown Sailor"
            log_file.write(f"{sailor_name}\n")

    print(f"Logged all sailor names being checked to 'checked_sailors_log.txt'.")

    # Audit CSV (everything we searched, with status)
    with open("searched_regattas.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["#", "Regatta Name", "Club", "Start Date (UTC)", "Results URL", "Status"])
        w.writerows(searched_rows)

    # Hits CSV
    with open("matches.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Regatta Name", "Club", "Start Date (UTC)", "Results URL"])
        w.writerows(matches)

    print("\n=== Summary ===")
    print(f"Searched: {len(searched_rows)} regattas (see searched_regattas.csv).")
    print(f"Matches for '{target_name}': {len(matches)} (see matches.csv).")

    
if __name__ == "__main__":
    main()
