import csv
import argparse
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Optional
import re
import unicodedata
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
def page_has_name(driver: webdriver.Chrome, target: str, timeout: int) -> Tuple[bool, str, List[str], List[str]]:
   """
   Returns:
     found_bool,
     detail,
     matched_list  -> list of row texts containing the target substring,
     all_rows_list -> list of ALL data-row texts seen on the page
   """
   target_low = (target or "").lower().strip()




   # Wait until either table body rows with TDs OR common virtualized data rows exist
   def any_rows_present(d):
       # true data rows in classic tables: tbody > tr that have at least one <td>
       if d.find_elements(By.CSS_SELECTOR, "table tbody tr td"):
           return True
       # virtualized/data-grid rows with grid cells (avoid headers)
       if d.find_elements(By.CSS_SELECTOR, "[role='row'] [role='gridcell'], .ag-row .ag-cell, .ReactVirtualized__Table__row .ReactVirtualized__Table__rowColumn, .MuiDataGrid-row .MuiDataGrid-cell, .rdg-row .rdg-cell"):
           return True
       return False




   try:
       WebDriverWait(driver, timeout).until(any_rows_present)
   except TimeoutException:
       return False, "timeout_waiting_for_rows", [], []




   import time




   # JS that returns ONLY data row lines (no headers). Each line joins cell texts with " | ".
   HARVEST_JS = r"""
const out = new Set();




// 1) Classic tables: only rows in <tbody> that have at least one <td>
document.querySelectorAll("table").forEach(tbl => {
 tbl.querySelectorAll("tbody tr").forEach(tr => {
   const tds = Array.from(tr.querySelectorAll("td"));
   if (tds.length === 0) return; // skip header-like rows
   const parts = tds.map(td => (td.innerText || td.textContent || "").trim()).filter(Boolean);
   const line = parts.join(" | ").trim();
   if (line) out.add(line);
 });
});




// 2) WAI-ARIA generic grids: only elements that have grid cells (skip header rows)
document.querySelectorAll("[role='row']").forEach(row => {
 const cells = Array.from(row.querySelectorAll("[role='gridcell'], [role='cell']"));
 if (cells.length === 0) return;
 const parts = cells.map(c => (c.innerText || c.textContent || "").trim()).filter(Boolean);
 const line = parts.join(" | ").trim();
 if (line) out.add(line);
});




// 3) AG Grid
document.querySelectorAll(".ag-row").forEach(row => {
 const cells = Array.from(row.querySelectorAll(".ag-cell"));
 if (cells.length === 0) return;
 const parts = cells.map(c => (c.innerText || c.textContent || "").trim()).filter(Boolean);
 const line = parts.join(" | ").trim();
 if (line) out.add(line);
});




// 4) React-Virtualized Table
document.querySelectorAll(".ReactVirtualized__Table__row").forEach(row => {
 const cells = Array.from(row.querySelectorAll(".ReactVirtualized__Table__rowColumn"));
 if (cells.length === 0) return;
 const parts = cells.map(c => (c.innerText || c.textContent || "").trim()).filter(Boolean);
 const line = parts.join(" | ").trim();
 if (line) out.add(line);
});




// 5) MUI DataGrid
document.querySelectorAll(".MuiDataGrid-row").forEach(row => {
 const cells = Array.from(row.querySelectorAll(".MuiDataGrid-cell"));
 if (cells.length === 0) return;
 const parts = cells.map(c => (c.innerText || c.textContent || "").trim()).filter(Boolean);
 const line = parts.join(" | ").trim();
 if (line) out.add(line);
});




// 6) react-data-grid
document.querySelectorAll(".rdg-row").forEach(row => {
 const cells = Array.from(row.querySelectorAll(".rdg-cell"));
 if (cells.length === 0) return;
 const parts = cells.map(c => (c.innerText || c.textContent || "").trim()).filter(Boolean);
 const line = parts.join(" | ").trim();
 if (line) out.add(line);
});




return Array.from(out);
"""




   def harvest_rows_text() -> List[str]:
       try:
           return driver.execute_script(HARVEST_JS) or []
       except Exception:
           return []




   def scroll_page():
       driver.execute_script("window.scrollBy(0, Math.max(600, window.innerHeight));")




   def scroll_element(el):
       driver.execute_script("arguments[0].scrollTop = arguments[0].scrollTop + arguments[0].clientHeight;", el)




   # Likely scrollable containers for virtualized tables
   scrollables = driver.find_elements(
       By.CSS_SELECTOR,
       "div[style*='overflow'], div[class*='scroll'], .ag-body-viewport, .MuiDataGrid-virtualScroller, .ReactVirtualized__Grid"
   )




   all_rows: List[str] = []
   matched: List[str] = []
   last_total = -1




   # Multiple passes with scrolling to force lazy rows to render
   for _ in range(16):
       rows_text = harvest_rows_text()




       # accumulate new lines
       for rt in rows_text:
           if rt not in all_rows:
               all_rows.append(rt)




       # check for matches on this pass
       if target_low:
           for rt in rows_text:
               if target_low in rt.lower() and rt not in matched:
                   matched.append(rt)




       # If no new rows are appearing, try one more big scroll then bail
       if len(all_rows) == last_total and len(all_rows) > 0:
           scroll_page()
           time.sleep(0.25)
           rows_text = harvest_rows_text()
           for rt in rows_text:
               if rt not in all_rows:
                   all_rows.append(rt)
           if len(all_rows) == last_total:
               break
       last_total = len(all_rows)




       # Scroll inner containers and the page
       for el in scrollables:
           try:
               scroll_element(el)
           except Exception:
               pass
       scroll_page()
       time.sleep(0.25)




   if matched:
       return True, "matched_in_rows", matched, all_rows
   if all_rows:
       return False, "name_not_found_in_rows", [], all_rows




   # Fallback: if we still saw nothing, try a full page text dump (not structured, but avoids empty CSV)
   try:
       page_text = driver.execute_script("return document.body.innerText || ''") or ""
       if page_text.strip():
           return False, "no_structured_rows_fallback", [], [page_text.strip()]
   except Exception:
       pass




   return False, "no_rows_found", [], []
























# ----------------------------
# Main
# ----------------------------
def main():
   args = parse_args()
   target_name = args.name.strip()
   contains_filter = args.contains.strip().lower()




   # New: date window (inclusive, UTC)
   start_bound = parse_cli_date(args.start_date)  # include if start >= this
   end_bound = parse_cli_date(args.end_date)      # include if start <= this




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
   matches = []        # hits only
   found_name_rows = []  # [Regatta, Club, Date, URL, Matched Name, Match Type]
   all_rows_log = []  # [Regatta, Club, Date, URL, Row #, Row Text]








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
               print(f"    [WARN] {status}")
               continue




           found, detail, matched_list, all_rows_list = page_has_name(driver, target_name, timeout=args.timeout)
           status = "FOUND" if found else detail
           searched_rows.append([idx, name, club, start_str, url, status])




           # Log ALL rows, even if no match
           for j, row_text in enumerate(all_rows_list, 1):
               all_rows_log.append([name, club, start_str, url, j, row_text])




           if found:
               matches.append([name, club, start_str, url])
               for mtxt in matched_list:
                   found_name_rows.append([name, club, start_str, url, mtxt])
               print(f"    [OK] Found {len(matched_list)} match(es) for '{target_name}'")
           else:
               print(f"    … {status}")












   finally:
       driver.quit()








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
   with open("all_rows.csv", "w", newline="", encoding="utf-8") as f:
       w = csv.writer(f)
       w.writerow(["Regatta Name", "Club", "Start Date (UTC)", "Results URL", "Row #", "Row Text"])
       w.writerows(all_rows_log)
   with open("results.csv", "w", newline="", encoding="utf-8") as f:
       w = csv.writer(f)
       w.writerow(["Regatta Name", "Club", "Start Date (UTC)", "Matched Row Text"])
       # found_name_rows rows are [name, club, start_str, url, mtxt]
       for name, club, start_str, url, mtxt in found_name_rows:
           w.writerow([name, club, start_str, mtxt])     
   with open("found_names.csv", "w", newline="", encoding="utf-8") as f:
       w = csv.writer(f)
       w.writerow(["Regatta Name", "Club", "Start Date (UTC)", "Results URL", "Matched Row Text"])
       w.writerows(found_name_rows)




   print("\n=== Summary ===")
   print(f"Searched: {len(searched_rows)} regattas (see searched_regattas.csv).")
   print(f"Matches for '{target_name}': {len(matches)} (see matches.csv).")
 








if __name__ == "__main__":
   main()