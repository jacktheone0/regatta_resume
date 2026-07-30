# updates729 — Admin panel plan (items 1–5 + DB ops)

## 0. Test setup (proposal only, nothing implemented)

Add `pytest` plus a single `tests/conftest.py`: an `app` fixture that builds the Flask app pointed at a `TEST_DATABASE_URL` Postgres DSN (a Neon branch is ideal) with `SCRAPER_ENABLED=false`, a Postgres connection fixture that opens a transaction per test and rolls it back, and a logged-in `client` fixture that inserts one user and posts the login form. Tests are route-level smoke tests over the admin surface only (the admin routes live directly on `app` in app.py — there is no blueprint, and extracting one is not part of this): `GET /admin` returns 200 logged in and redirects to `/login` logged out, and each `/api/scraper/*` POST returns a JSON body with a `success` key, with `threading.Thread` monkeypatched to a no-op so nothing actually scrapes. Nothing more — no coverage tooling, no factories, no CI wiring.

## 1. Remove the "Legacy Full Scrapers" panel

Files touched:
- `templates/admin.html` — delete the Legacy Full Scrapers block (heading, schools/sailors limit inputs, two run buttons; lines 79–102) and the two JS handlers for `runHSSchoolsBtn` / `runCollegeSchoolsBtn` (lines 283–377).
- `app.py` — delete routes `api_run_hs_schools_scraper` (`/api/scraper/run-hs-schools`, lines 541–572) and `api_run_college_schools_scraper` (`/api/scraper/run-college-schools`, lines 575–606).

The change: the panel, its two POST endpoints, and the JS that calls them are removed; the ClubSpot controls and the 3-step season controls are untouched. Unreachable-code note: `run_full_hs_scraper` and `run_full_college_scraper` in scraper_schools.py lose their only app callers — the sole remaining reference is the `if __name__ == '__main__'` test block at the bottom of that file. Their shared store helpers (`store_schools_in_db`, `store_sailors_in_db`, `store_results_in_db`) stay live via the 3-step functions, so nothing shared goes dead. scraper_schools.py and scraper.py are left untouched this session; the two dead functions can be deleted in the later ClubSpot/scraper session.

Verification: log in, open `/admin` — no "Legacy Full Scrapers" heading, no limit inputs; ClubSpot and 3-step sections still render. `curl -X POST -b <session cookie> https://<host>/api/scraper/run-hs-schools` → 404 page. Click "Step 1: Scrape Schools" → green alert `✓ Scraping HS schools...`.

## 2. Layout swap + SSE live log

Files touched:
- `models.py` — new `ScraperLogEntry` model (per-line log rows: id, created_at, level, message, step, season, source).
- `migrations/versions/005_add_scraper_log_entries.py` — Alembic migration for the new table (applies automatically on deploy, see DB ops).
- `scraper_schools.py` — attach a DB-writing `logging.Handler` to its logger so progress lines land in the new table.
- `app.py` — new `GET /admin/logs/stream` SSE endpoint (login required).
- `templates/admin.html` — right rail becomes the live log panel; DB stats move to a full-width section at the bottom.
- `gunicorn.conf.py` — new file: `worker_class = "gthread"`, `threads = 4`; gunicorn auto-loads it, so the `gunicorn app:app` start command is unchanged (per review decision).

Per-query progress is **not** currently written to `scraper_logs`: that table gets exactly one summary row per legacy ClubSpot run (written by scraper.py), and the 3-step pipeline writes its progress only to the Python logger, i.e. stdout. The logging change: a handler on the `scraper_schools` logger inserts one `scraper_log_entries` row per log record, using its own short-lived autocommit connection rather than `db.session` (the scraper commits `db.session` in 10-row batches mid-run, and the handler must not flush or roll back that state). The SSE endpoint is a generator that polls `scraper_log_entries` server-side every 2 s for `id >` the client's `Last-Event-ID`, yields each row as an event, and closes after ~50 s so a worker thread is never held indefinitely — `EventSource` reconnects automatically and resumes from the last id. The right rail renders these into a scrollable log box; the stats card moves to a full-width bottom section fed by the endpoint added in item 4.

Verification: click "Step 1: Scrape Schools"; log lines (e.g. `STEP 1: Scraping HS Schools`, `Stored N schools in database`) appear in the right rail within ~2 s. `curl -N -b <session cookie> https://<host>/admin/logs/stream` prints `id: <n>` / `data: {...}` frames and the connection closes on its own within ~50 s. `SELECT count(*) FROM scraper_log_entries;` grows during a run. DB stats render full-width at the page bottom.

Risk: gunicorn.conf.py changes server concurrency (1 sync worker → threaded); watch memory on the Render Starter instance after deploy.

## 3. "View Database" read-only browser

Files touched:
- `app.py` — new `GET /admin/db` route plus a hardcoded allowlist dict.
- `templates/admin_db.html` — new server-rendered table page.
- `templates/admin.html` — a "View Database" button linking to `/admin/db`.

The change: a module-level dict maps each browsable table name to its exact column list — `results`, `sailors`, `sailor_names`, `hs_results`, `college_results`, `regattas`, `schools`, `scraper_logs` (plus `scraper_log_entries` once item 2 lands). The route takes `table`, `sort`, `dir`, `page` query params; `table` and `sort` must be keys/members of the dict and `dir` must be `asc`/`desc`, otherwise 404 — the query is built only from the allowlisted identifiers, never from request strings. Fixed page size of 50 with prev/next links and a total-row count, column headers are links that toggle sort, all values rendered through Jinja autoescape, and there is no edit path and no SQL input anywhere.

Verification: `/admin/db?table=results` → 50 rows, pager showing `Page 1 of N (M rows)`. Click the `placement` header → rows reorder, arrow indicator flips on second click. `/admin/db?table=users` → 404. `/admin/db?table=results&sort=1;drop table x` → 404.

Risk: none real — worst case is a slow `ORDER BY` on an unindexed column of a large table, which paging caps.

## 4. Bug: admin page "reloads every ~5 seconds"

Files touched:
- `templates/admin.html` — remove the reload interval, mark status alerts permanent, refresh stats in place.
- `app.py` — small `GET /admin/stats.json` endpoint returning the three counts.

The cause is visible in the code and is two stacked behaviors. `templates/admin.html:513-516` runs `setInterval(() => location.reload(), 30000)` — a full page reload every 30 s that wipes button state and any status alert, so confirmations never survive. Separately, `static/js/main.js:11-19` auto-dismisses every Bootstrap `.alert` lacking the `alert-permanent` class 5 s after page load — so the info banner and any success/error confirmation vanish ~5 s after each reload, which is the ~5-second "elements refreshing" you see. Fix: delete the reload interval; fetch `/admin/stats.json` every 30 s and update the three stat numbers in place (this feeds item 2's bottom section); add `alert-permanent` to `#statusMessage` and the info banner so confirmations persist until replaced. If the symptom somehow survives this, one diagnostic: open DevTools → Network with "Preserve log" and watch for recurring `document`-type requests — their initiator column identifies any remaining reload source (meta refresh, extension), while their absence confirms it was the alert auto-dismiss.

Verification: click any scraper button — the green confirmation stays on screen for over a minute; the button re-enables after 10 s without the page flashing; Network tab shows only periodic `stats.json` XHRs, no `document` reloads; stat numbers still update after a scrape run.

## 5. Progress reporting for the 3-step season scraper

Files touched:
- `scraper_schools.py` — structured completion rows at each section boundary.
- `app.py` — latest-completion query exposed alongside `/admin/stats.json` (or the SSE payload).
- `templates/admin.html` — small "Last completed" status table above the live log rail.

Actual boundaries from scraper_schools.py (not three even phases): Step 1 `scrape_schools_only` has two sections — remote school-list scrape plus slug verification, then `store_schools_in_db` — and is season-independent (level only, hs/college). Step 2 `scrape_rosters_for_season` has two — roster scrape across all stored schools for the chosen season, then `store_sailors_in_db` committing in 10-row batches. Step 3 `scrape_results_for_season` has two — `scraper_v2.scrape_batch_sailors` over the sailor list, then `store_results_in_db`, also batched. The change: at the end of each section, write a `scraper_log_entries` row with `step`, `season`, `source` populated (the table from item 2 carries these columns for exactly this purpose); the admin page queries the latest row per (step, season, source) and renders lines like `Step 2 · store · f25 · HS · finished 14:32:07`, with season shown as `—` for Step 1 by design.

Verification: run Step 2 for f25/HS to completion; the status table shows both Step 2 sections with season `f25`, level `HS`, and timestamps matching the log rail; `SELECT step, season, source, max(created_at) FROM scraper_log_entries WHERE step IS NOT NULL GROUP BY 1,2,3;` returns matching rows.

Risk: Step 3 queries **all** sailors of the level, not just the chosen season's roster — the season it reports is the filter passed into scraper_v2, not a roster subset.

## DB ops

Written, not executed:
- `migrations/sql/001_select_faulty_sailors.sql` — candidate-bad-row SELECT over `sailors` plus a COUNT, flagging: blank name, `name_normalized` drift from `lower(btrim(name))`, junk characters (digits, `|/@#`), single-token names, duplicate `name_normalized`, and unclaimed rows with zero results. Each row carries a `reasons` array so you can eyeball which predicate to keep. No DELETE.
- `migrations/sql/002_add_admin_user_xavjav.sql` — inserts the `xavjav` row with `role='admin'` (the column is a free string; nothing enforces sailor/coach) and `ON CONFLICT (email) DO NOTHING`. The hash arrives as a psql variable generated from the `XAVJAV_PASSWORD` env var via `werkzeug.security.generate_password_hash` — the same call as `User.set_password` (models.py:24-25). No plaintext password appears in any file; the exact one-liner is in the file header.

How they get applied: one correction to the premise — the repo **does** have a migration tool: Flask-Migrate 4.0.7 is installed, `migrations/versions/` has revisions 001–004, and build.sh runs `flask db upgrade` on every Render deploy. So item 2's schema change ships as Alembic revision 005 and applies itself on deploy. These two files are one-off data/read operations, not schema, so they stay out of Alembic and run manually with `psql "$DATABASE_URL" -f <file>` against Neon (002 with the `-v password_hash=...` variable per its header).

Risk: per review decision, `'xavjav'` sits in the email column and cannot pass the login form's `Email()` validator, so the account cannot log in until that validator is relaxed in a later session.

## Order

1. **Item 1** — pure deletion, shrinks admin.html before any layout work. No dependencies.
2. **Item 4** — delete the reload interval and add `/admin/stats.json`; item 2's layout must be built on a page that isn't reloading itself.
3. **Item 2** — Alembic 005, log handler, SSE endpoint, layout swap, gunicorn.conf.py. Depends on item 4 (stats endpoint feeds the relocated stats section).
4. **Item 5** — depends on item 2's `scraper_log_entries` table, columns, and handler.
5. **Item 3** — independent of everything above; only touchpoint is adding `scraper_log_entries` to its allowlist.

Size flag: items 1 + 4 + 2 + 5 form one coherent implementation session. Adding item 3 on top makes the session too large — recommend splitting item 3 into its own session; nothing blocks it since its single dependency (the new table's name in the allowlist) can be added when it runs.
