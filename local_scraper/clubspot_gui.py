"""
Local ClubSpot scraper with a GUI, for testing the scraper on your own
computer (which has Chrome and open internet, unlike the Render host).

It uses the SAME Parse API call and the SAME results parsing as the
server's scraper.py, so if results show up here, the scraping logic
itself is proven and any server failure is an environment problem.

Setup (once):
    pip install selenium requests

Run:
    python clubspot_gui.py

Requires Google Chrome installed. Selenium downloads a matching
chromedriver automatically. Nothing is written to any database; use
"Export CSV" to save what was scraped.
"""
import csv
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait

# The regatta selection and results-row parsing are the SAME code the
# server scraper runs: clubspot_common.py at the repository root. Not a
# copy -- so what this GUI proves holds for the server, and vice versa.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clubspot_common import (HARVEST_JS, fetch_regattas, fetch_regatta_by_id,  # noqa: E402
                             parse_parse_date, parse_result_row, split_by_completion)


def extract_regatta_id(text):
    """Accept a bare regatta ID or any theclubspot.com/regatta/<id>/... URL"""
    text = (text or '').strip()
    match = re.search(r'regatta/([A-Za-z0-9]+)', text)
    if match:
        return match.group(1)
    if re.fullmatch(r'[A-Za-z0-9]{6,20}', text):
        return text
    return None


def make_driver(headless=True):
    """Local Chrome; Selenium Manager finds/downloads the driver"""
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--log-level=3")
    options.add_argument("--window-size=1280,900")
    options.page_load_strategy = "eager"
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    driver.set_script_timeout(30)
    return driver


def scrape_regatta_results(driver, regatta_id, timeout=12):
    """Load a regatta's results page and harvest parsed rows"""
    url = f"https://theclubspot.com/regatta/{regatta_id}/results?list_view=true"
    driver.get(url)

    def any_rows_present(d):
        return bool(
            d.find_elements("css selector", "table tbody tr td")
            or d.find_elements("css selector",
                               "[role='row'] [role='gridcell'], .ag-row .ag-cell")
        )

    try:
        WebDriverWait(driver, timeout).until(any_rows_present)
    except TimeoutException:
        return [], url

    for _ in range(8):
        driver.execute_script("window.scrollBy(0, Math.max(600, window.innerHeight));")
        time.sleep(0.2)

    rows_text = driver.execute_script(HARVEST_JS) or []
    results = [r for r in (parse_result_row(t) for t in rows_text) if r]
    return results, url


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ScraperGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ClubSpot Local Scraper")
        self.geometry("1000x700")

        self.msg_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.scraped_rows = []  # (regatta, sailor, placement, points, raw)

        self._build_widgets()
        self.after(100, self._drain_queue)

    def _build_widgets(self):
        controls = ttk.Frame(self, padding=8)
        controls.pack(fill='x')

        ttk.Label(controls, text="Start year:").pack(side='left')
        self.year_var = tk.StringVar(value="2024")
        ttk.Entry(controls, textvariable=self.year_var, width=6).pack(side='left', padx=(2, 10))

        ttk.Label(controls, text="Regattas to scrape:").pack(side='left')
        self.limit_var = tk.StringVar(value="3")
        ttk.Spinbox(controls, from_=1, to=50, textvariable=self.limit_var,
                    width=4).pack(side='left', padx=(2, 10))

        self.headless_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Headless Chrome",
                        variable=self.headless_var).pack(side='left', padx=(0, 6))

        # Finished = started AND past its endDate: upcoming regattas have no
        # results, in-progress ones only partial results
        self.past_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Finished regattas only",
                        variable=self.past_only_var).pack(side='left', padx=(0, 10))

        self.test_btn = ttk.Button(controls, text="1. Test API", command=self.test_api)
        self.test_btn.pack(side='left', padx=2)
        self.chrome_btn = ttk.Button(controls, text="2. Test Chrome", command=self.test_chrome)
        self.chrome_btn.pack(side='left', padx=2)
        self.run_btn = ttk.Button(controls, text="3. Run Scraper", command=self.run_scraper)
        self.run_btn.pack(side='left', padx=2)
        self.stop_btn = ttk.Button(controls, text="Stop", command=self.stop_event.set,
                                   state='disabled')
        self.stop_btn.pack(side='left', padx=2)
        ttk.Button(controls, text="Export CSV", command=self.export_csv).pack(side='left', padx=2)

        single = ttk.Frame(self, padding=(8, 0, 8, 4))
        single.pack(fill='x')
        ttk.Label(single, text="One test regatta (ID or URL):").pack(side='left')
        self.single_var = tk.StringVar()
        ttk.Entry(single, textvariable=self.single_var, width=50).pack(
            side='left', padx=4, fill='x', expand=True)
        self.single_btn = ttk.Button(single, text="Scrape This One",
                                     command=self.scrape_one)
        self.single_btn.pack(side='left', padx=2)

        self.status_var = tk.StringVar(value="Idle. Run steps 1-3 to verify the scraper.")
        ttk.Label(self, textvariable=self.status_var, padding=(8, 0)).pack(fill='x')

        panes = ttk.PanedWindow(self, orient='vertical')
        panes.pack(fill='both', expand=True, padx=8, pady=8)

        log_frame = ttk.LabelFrame(panes, text="Log")
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, state='disabled',
                                                  font=('Courier', 9))
        self.log_text.pack(fill='both', expand=True)
        panes.add(log_frame, weight=1)

        table_frame = ttk.LabelFrame(panes, text="Scraped results")
        columns = ('regatta', 'sailor', 'placement', 'points')
        self.tree = ttk.Treeview(table_frame, columns=columns, show='headings')
        for col, width in zip(columns, (340, 260, 90, 90)):
            self.tree.heading(col, text=col.title())
            self.tree.column(col, width=width, anchor='w')
        scroll = ttk.Scrollbar(table_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        panes.add(table_frame, weight=2)

    # -- queue plumbing (worker threads must not touch tk directly) --------

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == 'log':
                    self.log_text.configure(state='normal')
                    self.log_text.insert('end', payload + '\n')
                    self.log_text.see('end')
                    self.log_text.configure(state='disabled')
                elif kind == 'status':
                    self.status_var.set(payload)
                elif kind == 'row':
                    self.tree.insert('', 'end', values=payload[:4])
                elif kind == 'done':
                    self.run_btn.configure(state='normal')
                    self.test_btn.configure(state='normal')
                    self.chrome_btn.configure(state='normal')
                    self.single_btn.configure(state='normal')
                    self.stop_btn.configure(state='disabled')
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def log(self, message):
        self.msg_queue.put(('log', f"[{time.strftime('%H:%M:%S')}] {message}"))

    def set_status(self, message):
        self.msg_queue.put(('status', message))

    def _start_worker(self, target):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A task is already running.")
            return
        self.stop_event.clear()
        self.run_btn.configure(state='disabled')
        self.test_btn.configure(state='disabled')
        self.chrome_btn.configure(state='disabled')
        self.single_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    # -- actions -----------------------------------------------------------

    def test_api(self):
        year = int(self.year_var.get() or 0)
        past_only = self.past_only_var.get()

        def task():
            try:
                self.log("Querying ClubSpot Parse API for newest regattas...")
                regattas, total = fetch_regattas(year, limit=5, past_only=past_only)
                self.log(f"API OK -- {total} regattas available. Newest:")
                _, in_progress, future = split_by_completion(regattas)
                for r in regattas:
                    start = (r.get('startDate') or {}).get('iso', '?')[:10]
                    tag = ('  <-- FUTURE' if r in future
                           else '  <-- in progress' if r in in_progress else '')
                    self.log(f"  {start}  {r.get('name')}  ({r.get('objectId')}){tag}")
                if future:
                    self.log(f"WARNING: {len(future)} of these start in the future -- "
                             f"the API did not honor the startDate bound")
                self.set_status(f"API OK: {total} regattas available")
            except Exception as e:
                self.log(f"API FAILED: {e}")
                self.set_status("API failed -- see log")
            finally:
                self.msg_queue.put(('done', None))
        self._start_worker(task)

    def test_chrome(self):
        headless = self.headless_var.get()

        def task():
            driver = None
            try:
                self.log("Starting Chrome (Selenium Manager may download a driver "
                         "on first run, this can take a minute)...")
                driver = make_driver(headless)
                version = driver.capabilities.get('browserVersion', '?')
                self.log(f"Chrome OK -- version {version}")
                self.set_status(f"Chrome OK (version {version})")
            except Exception as e:
                self.log(f"CHROME FAILED: {e}")
                self.set_status("Chrome failed -- see log")
            finally:
                if driver:
                    driver.quit()
                self.msg_queue.put(('done', None))
        self._start_worker(task)

    def run_scraper(self):
        year = int(self.year_var.get() or 0)
        past_only = self.past_only_var.get()
        limit = max(1, int(self.limit_var.get() or 1))
        headless = self.headless_var.get()

        def task():
            driver = None
            try:
                self.log(f"Fetching {limit} newest "
                         f"{'finished ' if past_only else ''}regattas...")
                regattas, total = fetch_regattas(year, limit=limit, past_only=past_only)
                if past_only:
                    # Same second-stage check the server does: drop anything
                    # the API's date bound let through
                    regattas, in_progress, future = split_by_completion(regattas)
                    if future:
                        self.log(f"WARNING: dropped {len(future)} future regattas the API "
                                 f"returned despite the startDate bound")
                    if in_progress:
                        self.log(f"Skipping {len(in_progress)} regattas still in progress")
                self.log(f"Got {len(regattas)} regattas (of {total} available). Starting Chrome...")
                driver = make_driver(headless)
                self.log("Chrome started. Scraping results pages...")

                found_total = 0
                for i, regatta in enumerate(regattas, 1):
                    if self.stop_event.is_set():
                        self.log("Stopped by user.")
                        break
                    name = regatta.get('name', '?')
                    rid = regatta.get('objectId')
                    self.set_status(f"[{i}/{len(regattas)}] {name}")
                    self.log(f"[{i}/{len(regattas)}] {name} ({rid})")
                    try:
                        results, url = scrape_regatta_results(driver, rid)
                    except WebDriverException as e:
                        self.log(f"  Chrome error: {e}. Restarting browser...")
                        try:
                            driver.quit()
                        except Exception:
                            pass
                        driver = make_driver(headless)
                        continue
                    if not results:
                        self.log(f"  No results rows found at {url} "
                                 f"(regatta may have no posted results)")
                    for r in results:
                        row = (name, r['sailor_name'], r['placement'], r.get('points_scored'), r['raw_row_data'])
                        self.scraped_rows.append(row)
                        self.msg_queue.put(('row', row))
                    found_total += len(results)
                    self.log(f"  -> {len(results)} results parsed")
                    time.sleep(1)

                self.log(f"DONE. {found_total} results from this run.")
                self.set_status(
                    f"Done: {found_total} results. "
                    + ("SCRAPER WORKS -- server issues are environment problems."
                       if found_total else
                       "No results parsed -- inspect the log/pages.")
                )
            except Exception as e:
                self.log(f"RUN FAILED: {e}")
                self.set_status("Run failed -- see log")
            finally:
                if driver:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                self.msg_queue.put(('done', None))
        self._start_worker(task)

    def scrape_one(self):
        regatta_id = extract_regatta_id(self.single_var.get())
        if not regatta_id:
            messagebox.showinfo(
                "No regatta",
                "Paste a regatta ID or a theclubspot.com/regatta/... URL first.\n"
                "Tip: run '1. Test API' -- it lists IDs of recent regattas.")
            return
        headless = self.headless_var.get()

        def task():
            driver = None
            try:
                self.log(f"Looking up regatta {regatta_id}...")
                meta = fetch_regatta_by_id(regatta_id)
                if meta:
                    start = (meta.get('startDate') or {}).get('iso', '?')[:10]
                    name = meta.get('name', regatta_id)
                    self.log(f"Found: {name} (starts {start})")
                    if start > time.strftime('%Y-%m-%d'):
                        self.log("  NOTE: this regatta is in the future -- "
                                 "it likely has no results yet")
                else:
                    name = regatta_id
                    self.log("Regatta not found via the API; trying its results page anyway")

                self.log("Starting Chrome...")
                driver = make_driver(headless)
                results, url = scrape_regatta_results(driver, regatta_id)
                if not results:
                    self.log(f"No results rows found at {url}")
                for r in results:
                    row = (name, r['sailor_name'], r['placement'], r.get('points_scored'), r['raw_row_data'])
                    self.scraped_rows.append(row)
                    self.msg_queue.put(('row', row))
                self.log(f"DONE. {len(results)} results parsed from {name}.")
                self.set_status(
                    f"'{name}': {len(results)} results -- "
                    + ("SCRAPER WORKS." if results else "nothing parsed, see log.")
                )
            except Exception as e:
                self.log(f"SINGLE-REGATTA RUN FAILED: {e}")
                self.set_status("Single-regatta run failed -- see log")
            finally:
                if driver:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                self.msg_queue.put(('done', None))
        self._start_worker(task)

    def export_csv(self):
        if not self.scraped_rows:
            messagebox.showinfo("Nothing to export", "Run the scraper first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="clubspot_results.csv",
            filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['regatta', 'sailor', 'placement', 'points', 'raw_row'])
            writer.writerows(self.scraped_rows)
        self.log(f"Exported {len(self.scraped_rows)} rows to {path}")


if __name__ == '__main__':
    ScraperGUI().mainloop()
