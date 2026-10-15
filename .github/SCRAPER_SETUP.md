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

Each run lists every finished regatta ClubSpot has (paged, newest
first), skips the ones that already have results, skips the ones it has
already visited and found empty (re-checking those only for
`SCRAPER_RECHECK_EMPTY_DAYS` — 30 — after they ended, at most every two
days), and scrapes the rest until the time budget runs out. Every visit
is recorded in `clubspot_regatta_checks`, so an empty regatta is never
re-visited forever. The run summary reports what was listed, skipped,
visited, added, and how many regattas are **still unscraped** — that
number is the backlog, and it shrinks run by run.

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
