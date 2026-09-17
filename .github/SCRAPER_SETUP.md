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

- **Automatically:** Mondays at 06:00 UTC, which is Sunday 10:00 PM
  Pacific in winter and 11:00 PM in summer (GitHub cron is UTC only and
  does not follow daylight saving).
- **Manually:** the repository's **Actions** tab → *Weekly ClubSpot
  scrape* → **Run workflow**. Optional inputs let you cap the number of
  regattas (`limit`) or change the earliest year (`start_year`) — useful
  for a quick test run with `limit: 5`.
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
