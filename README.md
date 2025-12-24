# RegattaResume

A modern web application for sailors and coaches to track regatta results, build professional sailing resumes, and analyze performance trends.

## Features

### For Sailors
- **Profile Management**: View all your regatta results in one place
- **Resume Builder**: Create customizable sailing resumes
- **Export Options**: Download as PDF or create shareable links
- **Performance Tracking**: Track your progress over time
- **Customization**: Choose which results to showcase

### For Coaches
- **Sailor Search**: Find and evaluate sailors by name
- **Performance Analytics**: View detailed performance trends
- **Historical Comparison**: Compare recent vs past performance
- **Configurable Metrics**: Filter by date range, fleet, and more
- **Export Reports**: Generate analytics reports

### Technical Features
- Automated scraping from theclubspot.com (runs every Sunday at 11:59 PM)
- PostgreSQL database (Neon) for reliable data storage
- RESTful API for data access
- Responsive design for mobile and desktop
- Three resume templates (Modern, Classic, Minimal)

## Tech Stack

- **Backend**: Python 3.11, Flask
- **Database**: PostgreSQL (Neon)
- **Scraper**: BeautifulSoup4, Requests
- **PDF Generation**: WeasyPrint
- **Frontend**: Bootstrap 5, Chart.js
- **Deployment**: Render.com
- **Task Scheduling**: APScheduler

## Local Development Setup

### Prerequisites
- Python 3.11+
- PostgreSQL (or Neon account)
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
   ```bash
   cp .env.example .env
   ```

   Edit `.env` and add your configuration:
   ```
   FLASK_APP=app.py
   FLASK_ENV=development
   SECRET_KEY=your-secret-key-here
   DATABASE_URL=postgresql://user:password@host/database
   ```

5. **Initialize the database**
   ```bash
   flask db upgrade
   # or
   python -c "from app import app, db; app.app_context().push(); db.create_all()"
   ```

6. **Run the development server**
   ```bash
   flask run
   # or
   python app.py
   ```

   The app will be available at `http://localhost:5000`

## Deployment to Render.com

This app is configured for easy deployment to Render.com.

### Quick Deploy

1. **Push to GitHub**
   ```bash
   git add .
   git commit -m "Initial commit"
   git push origin main
   ```

2. **Connect to Render**
   - Go to [render.com](https://render.com)
   - Click "New +" → "Blueprint"
   - Connect your GitHub repository
   - Render will automatically detect `render.yaml`

3. **Set up Neon Database** (if not using Render PostgreSQL)
   - Create a Neon database at [neon.tech](https://neon.tech)
   - Copy the connection string
   - In Render dashboard, add environment variable:
     - Key: `DATABASE_URL`
     - Value: Your Neon connection string

4. **Deploy**
   - Click "Apply"
   - Render will build and deploy your app
   - Your app will be live at `https://your-app-name.onrender.com`

### Manual Deployment Steps

If you prefer manual setup:

1. Create a new Web Service on Render
2. Connect your GitHub repo
3. Configure:
   - **Build Command**: `./build.sh`
   - **Start Command**: `gunicorn app:app`
   - **Environment Variables**:
     - `FLASK_ENV=production`
     - `SECRET_KEY=<generate-secure-key>`
     - `DATABASE_URL=<your-neon-connection-string>`

4. Create a PostgreSQL database (or use external Neon)
5. Deploy!

## Usage

### Running the Scraper Manually

```bash
flask scrape
```

Or via the web interface (if logged in as admin):
```
POST /api/scraper/run
```

### Creating Migrations

When you modify database models:

```bash
flask db migrate -m "Description of changes"
flask db upgrade
```

## Project Structure

```
regatta_resume/
├── app.py                 # Main Flask application
├── models.py              # Database models
├── forms.py               # WTForms for authentication
├── utils.py               # Helper functions
├── scraper.py             # Web scraper for theclubspot.com
├── config.py              # Configuration settings
├── requirements.txt       # Python dependencies
├── build.sh              # Build script for Render
├── Procfile              # Process file for deployment
├── render.yaml           # Render deployment config
├── migrations/           # Database migrations
├── templates/            # Jinja2 HTML templates
│   ├── base.html
│   ├── index.html
│   ├── sailor_profile.html
│   ├── resume_builder.html
│   ├── coach_view.html
│   ├── resume_*.html     # Resume templates
│   └── ...
└── static/               # Static assets
    ├── css/
    │   └── style.css
    └── js/
        └── main.js
```

## API Endpoints

### Public Endpoints
- `GET /` - Landing page
- `GET /search?q=<name>` - Search sailors
- `GET /sailor/<id>` - Sailor profile
- `GET /coach-view/<id>` - Coach analytics view
- `GET /resume/<token>` - Shared resume

### Authenticated Endpoints
- `POST /login` - User login
- `POST /register` - User registration
- `GET /claim-profile` - Claim sailor profile
- `GET /sailor/<id>/resume-builder` - Resume builder

### API Routes
- `GET /api/sailors/<id>/stats` - Sailor statistics
- `GET /api/sailors/<id>/results` - Sailor results
- `POST /api/resume-link/create` - Create shareable resume
- `GET /api/resume-link/<token>/pdf` - Download PDF
- `POST /api/scraper/run` - Trigger scraper (admin)

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FLASK_ENV` | Environment (development/production) | development |
| `SECRET_KEY` | Flask secret key | Required |
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `SCRAPER_ENABLED` | Enable scheduled scraping | true |
| `ENABLE_REGISTRATION` | Allow new user registration | true |
| `ENABLE_PDF_EXPORT` | Enable PDF resume exports | true |

## Customizing the Scraper

The scraper in `scraper.py` is designed to work with theclubspot.com. To customize for your specific needs:

1. Update the selectors in `_extract_regatta_metadata()` and `_extract_results()`
2. Modify `_get_regatta_list()` to match the site's URL structure
3. Test with: `flask scrape`

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Troubleshooting

### Database Connection Issues
- Verify `DATABASE_URL` is correct
- Check Neon database is active
- Ensure IP whitelist allows connections

### Scraper Not Working
- Verify theclubspot.com structure hasn't changed
- Check network connectivity
- Review scraper logs

### PDF Generation Errors
- Ensure WeasyPrint dependencies are installed
- Check template syntax in `resume_*_pdf.html`

### Scheduler Not Running
- Verify `SCRAPER_ENABLED=true`
- Check server logs for errors
- Ensure timezone is set correctly

## License

MIT License - See LICENSE file for details

## Acknowledgments

- Data sourced from [TheClubSpot.com](https://theclubspot.com)
- Built with Flask, Bootstrap, and Chart.js
- Deployed on Render.com

## Support

For issues and questions:
- Open an issue on GitHub
- Check the troubleshooting section
- Review API documentation

---

**Built for sailors, by sailors.** ⛵
