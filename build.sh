#!/usr/bin/env bash
# Build script for Render deployment

set -o errexit  # Exit on error

echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Running database migrations..."
export FLASK_APP=app.py
export FLASK_ENV=production

# Run migrations
flask db upgrade

echo "Build complete!"
