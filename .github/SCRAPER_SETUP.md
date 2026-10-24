# Running the ClubSpot scraper on GitHub Actions

The ClubSpot scraper needs headless Chrome and roughly a gigabyte of RAM.
The Render web service has 512MB and no Chrome, so running the scraper
there OOM-killed the whole site. It now runs as a GitHub Actions workflow
(`.github/workflows/scrape.yml`) and writes to the same Neon database over
the network, so the website is never involved in scraping.

## One-time setup

### 0. Merge this workflow into the default branch (required)

GitHub only runs **scheduled** workflows from the repository's default
branch. While `scrape.yml` lives on a feature branch you can still start
runs by hand from the Actions tab, but the Sunday schedule will not fire
until the file is on `main`.

### 1. Add the `DATABASE_URL` secret (required)

In this repository: **Settings → Secrets and variables → Actions → New
repository secret**

- **Name:** `DATABASE_URL`
- **Value:** the Neon connection string (copy it from Render's environment
  variables, or from the Neon dashboard)

Without this the workflow stops at its "Check the database connection"
step with a clear message.

### 2. Let the admin button start a run (optional)

The **Run ClubSpot Scraper** button in `/admin` dispatches this workflow
through GitHub's API. For that it needs a token:

1. GitHub → **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**
2. Repository access: only `jacktheone0/regatta_resume`
3. Permissions: **Actions → Read and write**
4. In the Render dashboard, add the token as `GITHUB_WORKFLOW_TOKEN`

Skip this and the button simply explains that it is not configured and
links to the Actions tab, where **Run workflow** does the same job.

## Running it

- **Automatically:** daily at 06:00 UTC while the historical backlog is
  being drained (each run is bounded by `SCRAPER_TIME_BUDGET_MIN` and
  continues where the last one stopped). Once a run summary says
  **backlog drained**, switch the cron in `scrape.yml` back to weekly:
  `0 6 * * 1` (Monday 06:00 UTC = Sunday 10:00 PM Pacific in winter,
  11:00 PM in summer; GitHub cron is UTC only and does not follow
  daylight saving).
- **Manually:** the repository's **Actions** tab → *ClubSpot scrape* →
  **Run workflow**. Optional inputs let you cap the number of
  regattas (`limit`) or set an earliest year (`start_year`; blank scrapes
  all of ClubSpot's history) — useful for a quick test run with
  `limit: 5`. Runs are bounded by time, not by a regatta count: each run
  stops cleanly after `SCRAPER_TIME_BUDGET_MIN` minutes (300) and the
  next run continues where it left off.
- **From the admin panel:** the Run ClubSpot Scraper button, once the
  token above is configured.

## Watching a run

Each run prints progress in the Actions log and finishes with a summary
(status, regattas scraped, sailors added, results added). The same
progress also streams into the admin panel's live log, because the
scraper writes to `scraper_log_entries` in Neon exactly as before.

The admin **Stop Scraper** button also still works: it flags the running
job as cancelled in the database, and the workflow checks that flag
between regattas and exits cleanly.

## Notes

- Free minutes: unlimited on public repositories, 2,000/month on private
  ones. A weekly scrape uses a small fraction of that.
- Scheduled runs can be delayed a few minutes when GitHub is busy.
- If the repository goes 60 days without commits, GitHub disables
  scheduled workflows and emails you; re-enabling takes one click.
- `SCRAPER_ENABLED` stays `false` on Render so the web service never
  starts an in-process scrape. Setting it `true` locally restores the old
  behaviour for development.

## How the backlog drain works

Each run has two phases.

**1. Enumeration** (`clubspot_catalog.py`) keeps a catalogue of every
regatta ID ever seen in `clubspot_regatta_checks`. It never pages with
`skip`: the catalogue is walked one calendar quarter at a time
(`startDate` between X and Y, limit 1000), and a window that comes back
full is split in half and re-queried until no window is full. Two
markers in `scraper_state` bound the enumerated range and are updated
after every completed window, so an interrupted run resumes exactly
where it stopped:

- *Forward catch-up* first: from today plus a forward window
  (`SCRAPER_FORWARD_WINDOW_DAYS`, 180, so upcoming regattas enter the
  catalogue before they happen) back to the newest enumerated window.
  The most recent `SCRAPER_RECENT_RESCAN_DAYS` (60) are always re-walked
  because regattas get created or re-dated inside windows that were
  already enumerated; a `createdAt` query also picks up anything created
  since the last run wherever its date falls.
- *Historical backfill* second: from the oldest enumerated window further
  back, until `SCRAPER_ENUM_FLOOR_DATE` (2016-01-01) or
  `SCRAPER_ENUM_EMPTY_QUARTERS_DONE` (8) consecutive empty quarters.

Each run spends at most `SCRAPER_ENUM_REQUEST_BUDGET` (300) listing
requests or `SCRAPER_ENUM_TIME_BUDGET_MIN` (20) minutes on this, with a
`SCRAPER_ENUM_DELAY_SEC` (0.5) pause between requests; a 429 waits for
`Retry-After` and retries with exponential backoff, and only after six
failed attempts does the walk stop (markers saved, next run resumes).
Every run also records the step-0 filter counts and reads the sitemap,
and the run summary shows both. `SCRAPER_INCLUDE_ARCHIVED=true` drops
the `archived != true` filter if the counts show it hides real history.

**2. Scraping** visits catalogue rows that are due -- finished, never
visited, or visited-but-empty within `SCRAPER_RECHECK_EMPTY_DAYS` (30)
of their end date and not checked in the last two days -- newest first,
until the run's time budget. Regattas with results are also stored in
`regattas`; empty ones live only in the catalogue, so nothing is ever
re-visited forever. The summary's **still unscraped** number is the
backlog, and it shrinks run by run.

A regatta ID list from elsewhere (for example the probe script's
`--save` output from the sitemap) merges into the same catalogue with
`flask import-regatta-ids FILE`.

## Rolling back

The state of `main` before the backlog-drain changes is kept on the
branch `rollback/pre-backlog-drain`. The database must be downgraded
**before** the old code is deployed, because Render runs
`flask db upgrade` on every deploy and the old code does not know
migration 006.

```bash
# from a checkout of the CURRENT main (it has migration 006)
export DATABASE_URL='<the Neon connection string>'
scripts/rollback_backlog_drain.sh        # downgrades the DB, resets local main
git push --force-with-lease origin main  # publishes the rollback
```

The downgrade drops `clubspot_regatta_checks` and the run-counter columns
on `scraper_logs`; scraped regattas, sailors and results are untouched.
Render redeploys the old code from `main`, and the workflow file on
`main` goes back to the weekly schedule with it.
