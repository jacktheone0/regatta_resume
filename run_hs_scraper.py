#!/usr/bin/env python
"""
Quick script to run HS scraper locally
Usage: python run_hs_scraper.py
"""
import os
os.environ['FLASK_ENV'] = 'development'

from app import app
from scraper_hscollege import run_hs_scraper

if __name__ == '__main__':
    with app.app_context():
        print("Starting HS Sailing scraper...")
        print("This will scrape from scores.hssailing.org")
        print("Limit: 5 regattas (for testing)")
        print("-" * 50)

        # Run with small limit for testing
        stats = run_hs_scraper(limit=5, seasons=['s25', 'f24'])

        print("-" * 50)
        print("Scraper finished!")
        print(f"Stats: {stats}")
