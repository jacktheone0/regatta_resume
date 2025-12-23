# Regatta Resume Builder

A Flask web application that automatically generates professional sailing resumes by scraping regatta results from multiple sources.

## Features

- **Multi-Source Scraping**: Automatically pulls regatta data from:
  - Techscore (High School and College Sailing)
  - Clubspot (Club and Amateur Racing)
- **Smart Filtering**: Filter by date range, regatta name, and maximum results
- **Editable Results**: Interactive interface to edit and refine scraped data
- **Multiple PDF Styles**: Generate resumes in Classic, Modern, or Minimalist formats
- **Real-Time Progress**: Live streaming logs during data collection
- **Team History**: Add and edit sailing team affiliations

## Quick Start

### Prerequisites

- Python 3.8+
- Chrome/Chromium browser (for Selenium scraping)
- ChromeDriver (matching your Chrome version)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/regatta_resume.git
cd regatta_resume
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
cd regatta_resume
pip install -r requirements.txt
```

4. Run the application:
```bash
python app.py
```

5. Open your browser to `http://localhost:5000`

## Usage

### Basic Workflow

1. **Enter Sailor Information**
   - Enter the sailor's name (e.g., "Christopher Fulton")
   - Optionally set date range filters (start/end dates)
   - Set maximum number of regattas to check
   - Add regatta name filter if desired

2. **Data Collection**
   - The app scrapes Techscore and Clubspot in real-time
   - View progress logs as data is collected
   - Wait for completion (time varies based on filters)

3. **Review & Edit**
   - Review scraped results in the interactive table
   - Click cells to edit Source, Regatta, Date, Place, or Result
   - Add team history information

4. **Generate PDF**
   - Choose from three resume styles:
     - **Classic**: Traditional format with team section
     - **Modern**: Clean, contemporary design
     - **Minimalist**: Simple, compact layout
   - Download your professionally formatted resume

### Advanced Options

#### Command-Line Scraping

Run the scraper directly for debugging:

```bash
python Resume.py --name "Sailor Name" --start_date 2024-01-01 --end_date 2024-12-31 --max 100 --contains "Championship"
```

**Arguments:**
- `--name`: Sailor's name (required)
- `--start_date`: Start date in YYYY-MM-DD format (optional)
- `--end_date`: End date in YYYY-MM-DD format (optional)
- `--max`: Maximum regattas to check (default: 250)
- `--contains`: Filter regattas by name substring (optional)
- `--timeout`: Selenium wait timeout in seconds (default: 12)

#### Environment Variables

Create a `.env` file in the project root:

```env
FLASK_ENV=development
FLASK_DEBUG=True
SECRET_KEY=your-secret-key-here
MAX_REGATTAS=250
SCRAPER_TIMEOUT=12
```

## Project Structure

```
regatta_resume/
├── app.py                  # Main Flask application
├── Resume.py               # Clubspot scraper (Selenium)
├── scraper.py              # Techscore scraper (requests + BeautifulSoup)
├── resume_pdf.py           # PDF generation functions
├── config.py               # Configuration management
├── validators.py           # Input validation
├── logger.py               # Logging utilities
├── requirements.txt        # Python dependencies
├── Dockerfile              # Docker image definition
├── docker-compose.yml      # Docker Compose configuration
├── pytest.ini              # Testing configuration
├── .env.example            # Environment variables template
├── templates/              # HTML templates
│   ├── index.html         # Landing page
│   ├── loading.html       # Progress page
│   ├── resume.html        # Results editor
│   ├── resume_classic.html
│   ├── resume_modern.html
│   └── resume_minimalist.html
├── static/                 # Static assets and generated files
│   └── resumes/           # Pre-built PDFs
├── tests/                  # Unit tests
│   ├── test_validators.py
│   └── test_scraper.py
├── results.csv             # Clubspot scrape results (generated)
├── scraper_df.csv          # Techscore scrape results (generated)
├── teams.csv               # Team history data (generated)
└── matches.csv             # Matched regattas log (generated)
```

## Data Sources

### Techscore
- **URL Pattern**: `https://scores.{hssailing|collegesailing}.org/sailors/{sailor-name}/`
- **Method**: HTTP requests + BeautifulSoup
- **Data**: Regatta name, date, placement, result

### Clubspot
- **API**: `https://theclubspot.com/parse/classes/regattas`
- **Method**: Selenium WebDriver (handles dynamic JavaScript tables)
- **Data**: Regatta name, club, date, result row text

## Development

### Running in Debug Mode

```bash
export FLASK_ENV=development
export FLASK_DEBUG=True
python app.py
```

### Code Style

The project follows standard Python conventions:
- PEP 8 style guide
- Type hints where applicable
- Descriptive variable names

### Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Deployment

### Production Server

Use Gunicorn as the production WSGI server:

```bash
cd regatta_resume
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

### Docker Deployment

Coming soon! Docker support is planned for easier deployment.

### Environment Considerations

**For Selenium in production:**
- Install headless Chrome/Chromium
- Install ChromeDriver matching browser version
- Set `--headless=new` flag in Chrome options (already configured)

**For Render/Heroku:**
- Use buildpack for Chrome and ChromeDriver
- Increase timeout settings for slower environments
- Consider using Redis for caching API responses

## Troubleshooting

### Common Issues

**"ChromeDriver not found"**
- Install ChromeDriver: `brew install chromedriver` (Mac) or download from [ChromeDriver](https://chromedriver.chromium.org/)
- Ensure ChromeDriver is in PATH

**"Timeout waiting for rows"**
- Increase `--timeout` value
- Check internet connection
- Verify Clubspot website is accessible

**"No results found"**
- Verify sailor name spelling (case-insensitive)
- Check date range filters
- Try broader search criteria

**PDF generation fails**
- Ensure weasyprint dependencies are installed
- Check available disk space
- Verify write permissions in static/ directory

## Future Enhancements

Planned improvements include:

- [ ] Database integration (PostgreSQL/SQLite)
- [ ] User authentication and saved profiles
- [ ] Caching layer for API responses
- [ ] Rate limiting and request throttling
- [ ] Comprehensive test suite
- [ ] CI/CD pipeline
- [ ] Docker containerization
- [ ] Mobile-responsive design
- [ ] Export to additional formats (Word, HTML)
- [ ] Advanced analytics and statistics
- [ ] Social sharing features

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- [Techscore](https://scores.hssailing.org/) for high school and college sailing data
- [Clubspot](https://theclubspot.com/) for club racing data
- Flask and the Python community
- All sailors and regatta organizers

## Support

For issues, questions, or contributions:
- Open an issue on GitHub
- Contact the maintainers
- Check existing documentation

---

**Note**: This tool is for personal use only. Please respect the terms of service of data sources and avoid excessive scraping that could impact their services.
