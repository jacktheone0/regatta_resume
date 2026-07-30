# RegattaResume

A web application that aggregates competitive sailing results from high school and college sailing databases, and lets sailors build professional sailing resumes from their race history.

## What It Does

RegattaResume pulls results data from **scores.hssailing.org** and **scores.collegesailing.org**, stores it in a PostgreSQL database, and makes it searchable. Sailors can find their results, view performance statistics, and build shareable resumes. Coaches can pull up analytics views for any sailor in the database.

### For Sailors
- **Search**: Find your profile by name across both HS and college databases
- **Sailor Profile**: See all your regatta results, placements, and statistics in one place
- **Resume Builder**: Choose which results to feature, add a custom bio, and pick a template style (Modern, Classic, or Minimal)
- **Shareable Links**: Generate a public URL for your resume to send to coaches or colleges
- **PDF Export**: Download your resume as a PDF

### For Coaches
- **Sailor Search**: Look up any sailor in the database by name
- **Coach View**: Analytics dashboard showing recent vs. historical performance, configurable by time range
- **Fleet Breakdown**: See how a sailor performs across different boat classes
- **Performance Trends**: Visual performance trend data over time

### Admin / Data Management
- **3-Step Scraper** (admin dashboard):
  - **Step 1 – Scrape Schools**: Pulls all school listings from hssailing.org or collegesailing.org and stores them in the database
  - **Step 2 – Scrape Rosters**: For a selected season (e.g. `f25`, `s24`), fetches each school's roster and stores all sailor names
  - **Step 3 – Scrape Results**: Visits each sailor's individual results page and stores their full race history
- **Source Selection**: Each scrape step supports HS or College as separate data sources
- **Season Selection**: Target a specific season code (Fall/Spring + year, e.g. `f25`, `s25`, `f24`)
- **Batch DB Commits**: Results are committed to the database every 10 records to avoid connection timeouts on Neon
- **Searchbar Page**: `/searchbar` — a blank page with a Google Custom Search Engine embed for site-wide search

---

## Data Sources

| Source | URL | Data Collected |
|--------|-----|----------------|
| High School Sailing | scores.hssailing.org | Schools, rosters, regatta results |
| College Sailing | scores.collegesailing.org | Schools, rosters, regatta results |
| TheClubSpot | theclubspot.com | Legacy regatta results (weekly scheduled scrape) |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.11, Flask |
| Database | PostgreSQL (Neon) |
| ORM | SQLAlchemy, Flask-Migrate |
| Scraping | BeautifulSoup4, Requests |
| PDF Generation | WeasyPrint |
| Frontend | Bootstrap 5, Chart.js |
| Authentication | Flask-Login |
| Scheduling | APScheduler (Sunday 11:59 PM) |
| Deployment | Render.com (Starter plan) |

---

## Database Schema

| Table | Description |
|-------|-------------|
| `users` | User accounts (sailor or coach role) |
| `sailors` | Sailor profiles from TheClubSpot |
| `regattas` | Regatta events from TheClubSpot |
| `results` | Race results linking sailors to regattas (ClubSpot data) |
| `sailor_names` | All sailors from HS/College scrapers |
| `hs_results` | High school race results |
| `college_results` | College race results |
| `schools` | School listings from HS/College sites |
| `resume_links` | Shareable resume tokens with customization |
| `scraper_logs` | Log of scraper runs and outcomes |

---

## Local Development Setup

### Prerequisites
- Python 3.11+
- PostgreSQL or [Neon](https://neon.tech) account
- Git

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/regatta_resume.git
   cd regatta_resume
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**
   Create a `.env` file with:
   ```
   FLASK_APP=app.py
   FLASK_ENV=development
   SECRET_KEY=your-secret-key-here
   DATABASE_URL=postgresql://user:password@host/database
   ```

5. **Initialize the database**
   ```bash
   flask db upgrade
   ```

6. **Run the development server**
   ```bash
   flask run
   ```
   App runs at `http://localhost:5000`

---

## Deployment (Render.com)

1. Push to GitHub
2. In Render dashboard, create a new **Web Service** and connect the repo
3. Configure:
   - **Build Command**: `./build.sh`
   - **Start Command**: `gunicorn app:app`
   - **Environment Variables**: `FLASK_ENV`, `SECRET_KEY`, `DATABASE_URL`
4. The app deploys automatically on every push to `main`

> **Note**: Scraping runs as a background thread inside the web process. If a deployment happens mid-scrape, the thread will be killed. Data committed in batches up to that point is preserved. For long scrapes, consider moving to a Render Background Worker.

---

## Project Structure

```
regatta_resume/
├── app.py                  # Flask app, routes, scheduler
├── models.py               # SQLAlchemy models
├── forms.py                # WTForms (login, register, claim profile)
├── config.py               # Environment-based config
├── utils.py                # Stats, PDF generation, performance trends
├── scraper.py              # TheClubSpot scraper (legacy, scheduled)
├── scraper_schools.py      # Main orchestration: stores sailors/results in DB
├── school_scraper.py       # Scrapes school listings from HS/College sites
├── roster_scraper.py       # Scrapes season rosters for each school
├── scraper_v2.py           # Reference scraper for individual sailor results
├── migrations/             # Alembic database migrations
├── templates/              # Jinja2 HTML templates
│   ├── base.html
│   ├── index.html
│   ├── sailor_profile.html
│   ├── sailor_name_profile.html
│   ├── coach_view.html
│   ├── resume_builder.html
│   ├── resume_modern.html / resume_classic.html / resume_minimal.html
│   ├── resume_*_pdf.html   # PDF variants of each resume template
│   ├── admin.html          # Scraper control panel
│   ├── searchbar.html      # Google Custom Search embed
│   └── ...
└── static/
    ├── css/style.css
    └── js/main.js
```

---

## API Endpoints

### Public
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | Landing page |
| GET | `/search?q=<name>` | Search sailors (returns JSON) |
| GET | `/sailor/<id>` | Sailor profile (ClubSpot data) |
| GET | `/sailor-name/<id>` | Sailor profile (HS/College data) |
| GET | `/coach-view/<id>` | Coach analytics view |
| GET | `/resume/<token>` | View shared resume |
| GET | `/searchbar` | Google Custom Search page |

### Authenticated
| Method | Route | Description |
|--------|-------|-------------|
| GET/POST | `/login` | Login |
| GET/POST | `/register` | Register |
| GET | `/logout` | Logout |
| GET/POST | `/claim-profile` | Claim a sailor profile |
| GET | `/sailor/<id>/resume-builder` | Resume builder (owner only) |
| GET | `/admin` | Admin dashboard |

### API (Authenticated)
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/sailors/<id>/stats` | Sailor statistics |
| GET | `/api/sailors/<id>/results` | All sailor results |
| POST | `/api/resume-link/create` | Create shareable resume link |
| GET | `/api/resume-link/<token>/pdf` | Download resume as PDF |
| POST | `/api/scraper/run` | Run ClubSpot scraper |
| POST | `/api/scraper/stop` | Stop running scraper |
| POST | `/api/scraper/scrape-schools` | Step 1: Scrape school listings |
| POST | `/api/scraper/scrape-rosters` | Step 2: Scrape season rosters |
| POST | `/api/scraper/scrape-results` | Step 3: Scrape sailor results |

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FLASK_ENV` | `development` or `production` | `development` |
| `SECRET_KEY` | Flask secret key | Required |
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `SCRAPER_ENABLED` | Enable weekly scheduled scrape | `true` |
| `ENABLE_REGISTRATION` | Allow new registrations | `true` |
| `ENABLE_PDF_EXPORT` | Enable PDF downloads | `true` |

---

## Troubleshooting

**Database connection timeouts (Neon)**
Neon suspends idle connections. The scraper uses batches of 10 DB commits with retry logic and 500ms sleep between commits to avoid SSL timeout errors on long scraping runs.

**Scraper stops mid-run on Render**
Background threads die if the web process restarts (deploy, maintenance, or OOM). Data committed before the stop is preserved. To avoid this, move the scraper to a Render Background Worker service.

**College scraper scraping HS schools**
The `source_type` parameter must be passed through all three layers: `school_scraper.py`, `roster_scraper.py`, and `scraper_schools.py`. Verify each function receives and forwards `source_type` correctly.

**PDF generation errors**
Ensure WeasyPrint system dependencies are installed (`build.sh` handles this on Render).

---

## License

MIT License

---

*Built for sailors, by sailors.*
