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
    batch_size = 10  # Commit every 10 sailors
    total_rows = len(rosters_df)

    logger.info(f"Processing {total_rows} sailor records...")

    # Get all existing sailor names in batches to avoid connection timeout
    all_normalized_names = [row['Sailor_Name'].lower().strip() for _, row in rosters_df.iterrows()]

    # Query in batches of 10 to prevent SSL timeout on large datasets
    existing_sailors = []
    query_batch_size = 10
    for i in range(0, len(all_normalized_names), query_batch_size):
        batch = all_normalized_names[i:i + query_batch_size]

        # Retry logic for transient connection issues
        max_retries = 3
        for attempt in range(max_retries):
            try:
                batch_results = SailorName.query.filter(
                    SailorName.name_normalized.in_(batch)
                ).all()
                existing_sailors.extend(batch_results)
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Query batch {i//query_batch_size + 1} failed (attempt {attempt + 1}/{max_retries}), retrying...")
                    db.session.rollback()  # Rollback failed transaction
                    continue
                else:
                    logger.error(f"Query batch {i//query_batch_size + 1} failed after {max_retries} attempts")
                    raise

        if (i + query_batch_size) % 500 == 0:  # Progress every 500
            logger.info(f"Queried {min(i + query_batch_size, len(all_normalized_names))}/{len(all_normalized_names)} sailors...")

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

        # Commit in batches and give Neon a rest
        if (idx + 1) % batch_size == 0:
            db.session.commit()
            logger.info(f"Progress: {idx + 1}/{total_rows} ({(idx+1)/total_rows*100:.1f}%) - Added {sailors_added}, Updated {sailors_updated}")
            time.sleep(0.5)  # Give Neon 500ms rest between batches

    # Final commit
    db.session.commit()
    logger.info(f"✓ Stored {sailors_added} new sailors, updated {sailors_updated} existing sailors")


def store_results_in_db(results_df: pd.DataFrame, source_type: str):
    """Store results from DataFrame into database - optimized with batching"""
    results_added = 0
    batch_size = 10  # Commit every 10 results
    total_rows = len(results_df)

    logger.info(f"Processing {total_rows} result records...")

    # Pre-load all sailors to avoid repeated queries
    all_sailor_names = [row['Sailor_Name'].lower().strip() for _, row in results_df.iterrows()]
    sailors = SailorName.query.filter(
        SailorName.name_normalized.in_(all_sailor_names)
    ).all()
    sailor_lookup = {s.name_normalized: s for s in sailors}
    logger.info(f"Pre-loaded {len(sailor_lookup)} sailors")

    for idx, row in results_df.iterrows():
        sailor_name = row['Sailor_Name']
        name_normalized = sailor_name.lower().strip()

        # Find sailor in pre-loaded lookup
        sailor = sailor_lookup.get(name_normalized)
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

        # Commit in batches and give Neon a rest
        if (idx + 1) % batch_size == 0:
            db.session.commit()
            logger.info(f"Progress: {idx + 1}/{total_rows} ({(idx+1)/total_rows*100:.1f}%) - Added {results_added} results")
            time.sleep(0.5)  # Give Neon 500ms rest between batches

    # Final commit
    db.session.commit()
    logger.info(f"✓ Stored {results_added} new results in database")


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


# ============================================================================
# SEASON-SPECIFIC SCRAPING FUNCTIONS
# ============================================================================

def scrape_schools_only(source_type='hs'):
    """
    Step 1: Scrape all schools and store in database
    Note: Schools don't change by season, so this scrapes all schools

    Returns:
        Number of schools stored
    """
    from app import app

    with app.app_context():
        logger.info("="*70)
        logger.info(f"  STEP 1: Scraping {source_type.upper()} Schools")
        logger.info("="*70)

        # Use user's school_scraper
        schools_df = school_scraper.scrape_schools()

        if schools_df.empty:
            logger.error("No schools found")
            return 0

        schools_df = school_scraper.verify_url_slugs(schools_df)

        # Store schools in database
        store_schools_in_db(schools_df, source_type)

        logger.info(f"✓ Stored {len(schools_df)} schools")
        return len(schools_df)


def scrape_rosters_for_season(season_code: str, source_type='hs'):
    """
    Step 2: Scrape rosters for a specific season using schools from database

    Args:
        season_code: Season to scrape (e.g., "f25", "s24")
        source_type: 'hs' or 'college'

    Returns:
        Number of sailors stored
    """
    from app import app

    with app.app_context():
        logger.info("="*70)
        logger.info(f"  STEP 2: Scraping {source_type.upper()} Rosters for {season_code.upper()}")
        logger.info("="*70)

        # Get schools from database
        schools = School.query.filter_by(source=source_type).all()

        if not schools:
            logger.error(f"No schools found in database for {source_type}. Run Step 1 first.")
            return 0

        logger.info(f"Found {len(schools)} schools in database")

        # Convert to DataFrame format expected by roster_scraper
        schools_data = []
        for school in schools:
            schools_data.append({
                'School_Name': school.name,
                'URL_Slug': school.url_slug,
                'District': school.district or '',
                'Full_URL': school.full_url or ''
            })

        schools_df = pd.DataFrame(schools_data)

        # Use user's roster_scraper for single season
        rosters_df = roster_scraper.scrape_all_rosters(schools_df, seasons=[season_code])

        if rosters_df.empty:
            logger.warning(f"No rosters found for season {season_code}")
            return 0

        # Store sailors in database
        store_sailors_in_db(rosters_df, source_type)

        logger.info(f"✓ Stored {len(rosters_df)} sailor records for {season_code}")
        return len(rosters_df)


def scrape_results_for_season(season_code: str, source_type='hs'):
    """
    Step 3: Scrape results for sailors from a specific season using sailors from database

    Args:
        season_code: Season to scrape results for (e.g., "f25", "s24")
        source_type: 'hs' or 'college'

    Returns:
        Number of results stored
    """
    from app import app

    with app.app_context():
        logger.info("="*70)
        logger.info(f"  STEP 3: Scraping {source_type.upper()} Results for {season_code.upper()}")
        logger.info("="*70)

        # Get sailors from database (all sailors for now - season filtering happens in results)
        if source_type == 'hs':
            sailors = SailorName.query.filter(SailorName.source.in_(['hs', 'both'])).all()
        else:
            sailors = SailorName.query.filter(SailorName.source.in_(['college', 'both'])).all()

        if not sailors:
            logger.error(f"No sailors found in database for {source_type}. Run Step 2 first.")
            return 0

        logger.info(f"Found {len(sailors)} sailors in database")

        # Convert to DataFrame format expected by scraper_v2
        sailors_data = []
        for sailor in sailors:
            sailors_data.append({
                'Sailor_Name': sailor.name,
                'School': sailor.school or '',
                'Season': season_code[0].upper() + 'all' if season_code[0] in ['f', 's'] else '',
                'Year': season_code[1:] if len(season_code) > 1 else ''
            })

        sailors_df = pd.DataFrame(sailors_data)

        # Use user's scraper_v2
        results_df = scraper_v2.scrape_batch_sailors(sailors_df, delay=0.5, save_interval=50)

        if results_df.empty:
            logger.warning(f"No results found for season {season_code}")
            return 0

        # Store results in database
        store_results_in_db(results_df, source_type)

        logger.info(f"✓ Stored {len(results_df)} results for {season_code}")
        return len(results_df)


if __name__ == '__main__':
    # Test with limited scope
    run_full_hs_scraper(limit_schools=2, limit_sailors=10)
