# Local ClubSpot Scraper (GUI)

A standalone desktop tool to verify the ClubSpot scraper on your own
computer, where Chrome and open internet are available. It uses the
**same Parse API call and the same results parsing as `scraper.py` on
the server**, so:

- results appearing here ⇒ the scraping logic works, and any server
  failure is an environment problem (Chrome install, memory, network)
- no results here ⇒ the parsing itself needs fixing, and the Log pane +
  exported `raw_row` column show exactly what the pages returned

Nothing touches the database. It's read-only against theclubspot.com.

## Setup

Needs Python 3.10+ and Google Chrome installed.

```bash
pip install selenium requests
python local_scraper/clubspot_gui.py
```

(Selenium downloads a matching chromedriver automatically on first run.
On Linux you may also need tkinter: `sudo apt install python3-tk`.
Windows and macOS Pythons include it.)

## Using it

Work through the numbered buttons:

1. **Test API** – confirms the regatta list endpoint answers and shows
   the newest regattas.
2. **Test Chrome** – confirms Selenium can start your local Chrome.
3. **Run Scraper** – scrapes the N newest regattas' results pages and
   fills the results table live. The status bar gives the verdict.

Uncheck **Headless Chrome** to watch the browser work in a visible
window. **Export CSV** saves everything scraped, including the raw
pipe-separated row text used for parsing (useful for debugging).
