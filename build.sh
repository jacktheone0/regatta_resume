#!/usr/bin/env bash
# Build script for Render deployment

set -o errexit  # Exit on error

echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# No Chrome install here on purpose: the ClubSpot scraper runs on GitHub
# Actions (.github/workflows/scrape.yml), where Chrome is pre-installed and
# memory is not capped at 512MB.

echo "Running database migrations..."
export FLASK_APP=app.py
export FLASK_ENV=production

# Run migrations
flask db upgrade

echo "Build complete!"
