#!/usr/bin/env bash
# Build script for Render deployment

set -o errexit  # Exit on error

echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Installing headless Chrome for the ClubSpot scraper..."
# Render's native Python runtime ships no Chrome, so fetch a portable
# chrome-headless-shell + matching chromedriver into the project dir
# (scraper.py auto-detects them under .chrome/). Best-effort: a failure
# must not block the site deploy.
install_chrome() {
    local version base
    version=$(curl -sS "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json" \
        | python3 -c "import sys, json; print(json.load(sys.stdin)['channels']['Stable']['version'])") \
        && base="https://storage.googleapis.com/chrome-for-testing-public/$version/linux64" \
        && mkdir -p .chrome \
        && curl -sSL "$base/chrome-headless-shell-linux64.zip" -o /tmp/chrome-headless-shell.zip \
        && curl -sSL "$base/chromedriver-linux64.zip" -o /tmp/chromedriver.zip \
        && python3 -c "
import zipfile
for name in ('chrome-headless-shell', 'chromedriver'):
    zipfile.ZipFile(f'/tmp/{name}.zip').extractall('.chrome')
" \
        && chmod +x .chrome/chrome-headless-shell-linux64/* .chrome/chromedriver-linux64/chromedriver \
        && .chrome/chromedriver-linux64/chromedriver --version \
        && .chrome/chrome-headless-shell-linux64/chrome-headless-shell --version \
        && echo "Installed Chrome for Testing $version"
}
install_chrome || echo "WARNING: Chrome install failed; the ClubSpot scraper will not be able to run"

echo "Running database migrations..."
export FLASK_APP=app.py
export FLASK_ENV=production

# Run migrations
flask db upgrade

echo "Build complete!"
