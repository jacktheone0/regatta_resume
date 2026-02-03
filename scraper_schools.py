"""
Integration layer: Uses user's proven scrapers and stores results in database
"""
import pandas as pd
from datetime import datetime
import time
import logging
import re

# Import user's proven scrapers
import school_scraper
import roster_scraper
import scraper_v2

# Import database models
from models import db, SailorName, HSResult, CollegeResult, School

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def store_schools_in_db(schools_df: pd.DataFrame, source_type: str):
    """Store schools from DataFrame into database"""
    for idx, row in schools_df.iterrows():
        # Check if already exists
        school = School.query.filter_by(
            url_slug=row['URL_Slug'],
            source=source_type
        ).first()

        if not school:
            school = School(
                name=row['School_Name'],
                district=row.get('District', ''),
                source=source_type,
                url_slug=row['URL_Slug'],
                full_url=row.get('Full_URL', '')
            )
            db.session.add(school)
        else:
            if not school.district and row.get('District'):
                school.district = row['District']
            school.updated_at = datetime.utcnow()

    db.session.commit()
    logger.info(f"Stored {len(schools_df)} schools in database")


def store_sailors_in_db(rosters_df: pd.DataFrame, source_type: str):
    """Store sailor names from DataFrame into database - optimized with batching"""
    sailors_added = 0
    sailors_updated = 0
    batch_size = 1000
    total_rows = len(rosters_df)

    logger.info(f"Processing {total_rows} sailor records...")

    # Get all existing sailor names in one query (much faster than N queries)
    all_normalized_names = [row['Sailor_Name'].lower().strip() for _, row in rosters_df.iterrows()]
    existing_sailors = SailorName.query.filter(
        SailorName.name_normalized.in_(all_normalized_names)
    ).all()

    # Create lookup dict for fast access
    existing_dict = {s.name_normalized: s for s in existing_sailors}
    logger.info(f"Found {len(existing_dict)} existing sailors in database")

    # Process in batches
    for idx, row in rosters_df.iterrows():
        sailor_name = row['Sailor_Name']
        school = row.get('School', '')
        name_normalized = sailor_name.lower().strip()

        if name_normalized in existing_dict:
            # Update existing sailor
            sailor = existing_dict[name_normalized]

            if sailor.source != 'both':
                if (sailor.source == 'hs' and source_type == 'college') or \
                   (sailor.source == 'college' and source_type == 'hs'):
                    sailor.source = 'both'
                    sailors_updated += 1

            if not sailor.school:
                sailor.school = school
                sailors_updated += 1
        else:
            # Create new sailor
            sailor = SailorName(
                name=sailor_name.strip(),
                name_normalized=name_normalized,
                school=school,
                source=source_type
            )
            db.session.add(sailor)
            existing_dict[name_normalized] = sailor
            sailors_added += 1

        # Commit in batches to avoid memory issues
        if (idx + 1) % batch_size == 0:
            db.session.commit()
            logger.info(f"Progress: {idx + 1}/{total_rows} ({(idx+1)/total_rows*100:.1f}%) - Added {sailors_added}, Updated {sailors_updated}")

    # Final commit
    db.session.commit()
    logger.info(f"✓ Stored {sailors_added} new sailors, updated {sailors_updated} existing sailors")


def store_results_in_db(results_df: pd.DataFrame, source_type: str):
    """Store results from DataFrame into database"""
    results_added = 0

    for idx, row in results_df.iterrows():
        sailor_name = row['Sailor_Name']
        name_normalized = sailor_name.lower().strip()

        # Find sailor in database
        sailor = SailorName.query.filter_by(name_normalized=name_normalized).first()
        if not sailor:
            logger.warning(f"Sailor not found: {sailor_name}")
            continue

        # Parse place and total from "21/32" format
        if pd.notna(row.get('Place')) and pd.notna(row.get('Total')):
            place_numeric = int(row['Place'])
            total_boats = int(row['Total'])
            place_str = f"{place_numeric}/{total_boats}"
        else:
            continue

        # Parse date
        regatta_date = None
        if pd.notna(row.get('Date')):
            try:
                regatta_date = datetime.strptime(row['Date'], '%m/%d/%Y').date()
            except:
                pass

        # Build raw row data
        raw_row = f"{row['Regatta']} | {row['Result']} | {row.get('Source', '')} | {row.get('Date', '')}"

        # Create result record
        result_data = {
            'sailor_name_id': sailor.id,
            'regatta_name': row['Regatta'],
            'regatta_link': None,  # User's scraper doesn't capture links
            'regatta_date': regatta_date,
            'place': place_str,
            'place_numeric': place_numeric,
            'total_boats': total_boats,
            'position': None,  # Not in user's scraper
            'division': None,  # Not in user's scraper
            'school': sailor.school,
            'raw_row_data': raw_row
        }

        # Store in appropriate table
        if source_type == 'hs' and row.get('Source') == 'HS':
            existing = HSResult.query.filter_by(
                sailor_name_id=sailor.id,
                regatta_name=row['Regatta']
            ).first()

            if not existing:
                result = HSResult(**result_data)
                db.session.add(result)
                results_added += 1

        elif source_type == 'college' and row.get('Source') == 'College':
            existing = CollegeResult.query.filter_by(
                sailor_name_id=sailor.id,
                regatta_name=row['Regatta']
            ).first()

            if not existing:
                result = CollegeResult(**result_data)
                db.session.add(result)
                results_added += 1

    db.session.commit()
    logger.info(f"Stored {results_added} new results in database")


def run_full_hs_scraper(limit_schools=None, limit_sailors=None):
    """
    Full HS scraper using user's proven 3-step approach

    Args:
        limit_schools: Max schools to scrape (None = all)
        limit_sailors: Max sailors to scrape results for (None = all)
    """
    from app import app

    with app.app_context():
        logger.info("="*70)
        logger.info("  STEP 1: Scraping HS Schools")
        logger.info("="*70)

        # Use user's school_scraper
        schools_df = school_scraper.scrape_schools()

        if schools_df.empty:
            logger.error("No schools found")
            return

        schools_df = school_scraper.verify_url_slugs(schools_df)

        if limit_schools:
            schools_df = schools_df.head(limit_schools)
            logger.info(f"Limited to {limit_schools} schools")

        # Store schools in database
        store_schools_in_db(schools_df, 'hs')

        logger.info("="*70)
        logger.info("  STEP 2: Scraping HS Rosters")
        logger.info("="*70)

        # Use user's roster_scraper
        seasons = ["f25", "s25", "f24", "s24", "f23", "s23", "f22", "s22"]
        rosters_df = roster_scraper.scrape_all_rosters(schools_df, seasons)

        if rosters_df.empty:
            logger.error("No rosters found")
            return

        # Store sailors in database
        store_sailors_in_db(rosters_df, 'hs')

        logger.info("="*70)
        logger.info("  STEP 3: Scraping Individual Sailor Results")
        logger.info("="*70)

        # Limit sailors if requested
        if limit_sailors:
            rosters_df = rosters_df.head(limit_sailors)
            logger.info(f"Limited to {limit_sailors} sailors")

        # Use user's scraper_v2
        results_df = scraper_v2.scrape_batch_sailors(rosters_df, delay=0.5, save_interval=50)

        if not results_df.empty:
            # Store results in database
            store_results_in_db(results_df, 'hs')

        logger.info("="*70)
        logger.info("  HS SCRAPING COMPLETE")
        logger.info("="*70)


def run_full_college_scraper(limit_schools=None, limit_sailors=None):
    """
    Full College scraper using user's proven 3-step approach

    Note: User's school_scraper.py is hardcoded for HS URL, so we need to modify it
    or scrape college schools differently. For now, we'll skip college schools.
    """
    from app import app

    with app.app_context():
        logger.info("="*70)
        logger.info("  COLLEGE SCRAPER NOT YET IMPLEMENTED")
        logger.info("  (User's school_scraper.py is HS-only)")
        logger.info("="*70)


if __name__ == '__main__':
    # Test with limited scope
    run_full_hs_scraper(limit_schools=2, limit_sailors=10)
