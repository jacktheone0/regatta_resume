"""
Integration layer: Uses user's proven scrapers and stores results in database
"""
import pandas as pd
from datetime import date, datetime
import time
import logging
import re

# Import user's proven scrapers
import school_scraper
import roster_scraper
import scraper_v2

# Import database models
from models import db, SailorName, HSResult, CollegeResult, School

from dateutil import parser as dateutil_parser

from scraper_logging import db_log as _db_log, attach_db_log_handler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _attach_db_log_handler():
    """Attach the shared DB handler once; call from inside an app context."""
    attach_db_log_handler(logger)


# Sentinel year: dateutil fills missing fields from the default, so a parsed
# year of 1900 means the site's date text carried no year and can't be trusted
_DATE_DEFAULT = datetime(1900, 1, 1)

# The yearless shape the sailor pages actually print: 'Apr 20', 'Mar 06'.
# Gating the season-code fallback on this keeps a bare numeric range like
# '4-5' from being handed a year it can't justify.
_MONTH_DAY_RE = re.compile(
    r'^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})$',
    re.IGNORECASE)

_SEASON_CODE_RE = re.compile(r'^([fs])(\d{2})$', re.IGNORECASE)


def _year_for_season(season_code, month):
    """
    TechScore season codes sit inside one calendar year: f24 runs Sep-Dec
    2024, s26 runs Feb-Jun 2026. The only realistic wrap is a fall regatta
    sailed in the new year, which belongs to the following one.
    """
    if not season_code:
        return None
    match = _SEASON_CODE_RE.match(str(season_code).strip())
    if not match:
        return None
    year = 2000 + int(match.group(2))
    if match.group(1).lower() == 'f' and month <= 6:
        year += 1
    return year


def _parse_regatta_date(value, season_code=None):
    """
    Parse the regatta date text from a sailor page; None if unparseable.

    season_code comes from the regatta link (/s26/...) and supplies the year
    for the yearless dates the sailor pages print.
    """
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s:
        return None

    # Exact formats on the full string first (before any range handling,
    # which would corrupt ISO dates)
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    # Range collapsed to its start ("Oct 4-5, 2024" -> "Oct 4, 2024"), then
    # the left side of a full range like "10/4/2024-10/5/2024", then the raw
    # string last -- dateutil misreads a bare range like "4-5" as a year
    candidates = []
    collapsed = re.sub(r'(\d)\s*[-–]\s*\d+', r'\1', s)
    if collapsed != s:
        candidates.append(collapsed)
    for sep in ('-', '–'):
        left = s.split(sep)[0].strip()
        if left and left != s and left not in candidates:
            candidates.append(left)
    candidates.append(s)

    for candidate in candidates:
        try:
            return datetime.strptime(candidate, '%m/%d/%Y').date()
        except ValueError:
            pass
        # Without an explicit 4-digit year dateutil invents one (from a
        # range like "4-5" or today's date), so don't trust the result --
        # unless the regatta link gave us a season to take the year from
        if not re.search(r'\d{4}', candidate):
            season_year = None
            if _MONTH_DAY_RE.match(candidate):
                try:
                    month_day = dateutil_parser.parse(candidate, default=_DATE_DEFAULT)
                except (ValueError, OverflowError):
                    continue
                season_year = _year_for_season(season_code, month_day.month)
            if season_year is None:
                continue
            return date(season_year, month_day.month, month_day.day)
        try:
            parsed = dateutil_parser.parse(candidate, default=_DATE_DEFAULT)
            if parsed.year != _DATE_DEFAULT.year:
                return parsed.date()
        except (ValueError, OverflowError):
            continue

    logger.warning(f"Could not parse regatta date '{s}' (season {season_code or 'unknown'})")
    return None


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
    results_updated = 0
    batch_size = 10  # Commit every 10 results
    total_rows = len(results_df)

    logger.info(f"Processing {total_rows} result records...")

    # Pre-load all sailors in batches to avoid SSL timeout
    all_sailor_names = [row['Sailor_Name'].lower().strip() for _, row in results_df.iterrows()]

    # Query in batches of 10 to prevent SSL timeout
    all_sailors = []
    query_batch_size = 10
    for i in range(0, len(all_sailor_names), query_batch_size):
        batch = all_sailor_names[i:i + query_batch_size]

        # Retry logic for transient connection issues
        max_retries = 3
        for attempt in range(max_retries):
            try:
                batch_results = SailorName.query.filter(
                    SailorName.name_normalized.in_(batch)
                ).all()
                all_sailors.extend(batch_results)
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Query batch {i//query_batch_size + 1} failed (attempt {attempt + 1}/{max_retries}), retrying...")
                    db.session.rollback()
                    time.sleep(1)  # Wait before retry
                    continue
                else:
                    logger.error(f"Query batch {i//query_batch_size + 1} failed after {max_retries} attempts")
                    raise

        if (i + query_batch_size) % 100 == 0:  # Progress every 100
            logger.info(f"Pre-loaded {min(i + query_batch_size, len(all_sailor_names))}/{len(all_sailor_names)} sailors...")

    sailor_lookup = {s.name_normalized: s for s in all_sailors}
    logger.info(f"✓ Pre-loaded {len(sailor_lookup)} sailors")

    # Pre-load existing results to avoid repeated queries
    sailor_ids = [s.id for s in all_sailors]
    if source_type == 'hs':
        existing_results = HSResult.query.filter(HSResult.sailor_name_id.in_(sailor_ids)).all()
        existing_lookup = {(r.sailor_name_id, r.regatta_name): r for r in existing_results}
    else:
        existing_results = CollegeResult.query.filter(CollegeResult.sailor_name_id.in_(sailor_ids)).all()
        existing_lookup = {(r.sailor_name_id, r.regatta_name): r for r in existing_results}
    logger.info(f"✓ Pre-loaded {len(existing_lookup)} existing results")

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

        # Parse date (multiple site formats; warns on strings it can't read).
        # The season code from the regatta link carries the year, which the
        # date cell on the sailor pages leaves out.
        season_code = row.get('Regatta_Season') if pd.notna(row.get('Regatta_Season')) else None
        regatta_date = _parse_regatta_date(row.get('Date'), season_code)

        regatta_link = row.get('Regatta_Link') if pd.notna(row.get('Regatta_Link')) else None
        position = row.get('Position') if pd.notna(row.get('Position')) else None
        division = row.get('Division') if pd.notna(row.get('Division')) else None

        # Build raw row data
        raw_row = (f"{row['Regatta']} | {row['Result']} | {position or ''} | "
                   f"{row.get('Source', '')} | {row.get('Date', '')}")

        # Create result record
        result_data = {
            'sailor_name_id': sailor.id,
            'regatta_name': row['Regatta'],
            'regatta_link': regatta_link,
            'regatta_date': regatta_date,
            'place': place_str,
            'place_numeric': place_numeric,
            'total_boats': total_boats,
            'position': position,
            'division': division,
            'school': sailor.school,
            'raw_row_data': raw_row
        }

        # Store in appropriate table (check pre-loaded existing results)
        result_key = (sailor.id, row['Regatta'])

        matches_source = (source_type == 'hs' and row.get('Source') == 'HS') or \
                         (source_type == 'college' and row.get('Source') == 'College')

        if matches_source:
            if result_key in existing_lookup:
                # Backfill date/position/division on rows stored before the
                # scraper captured them
                existing = existing_lookup[result_key]
                updated = False
                if existing.regatta_date is None and regatta_date:
                    existing.regatta_date = regatta_date
                    updated = True
                if existing.regatta_link is None and regatta_link:
                    existing.regatta_link = regatta_link
                    updated = True
                if existing.position is None and position:
                    existing.position = position
                    updated = True
                if existing.division is None and division:
                    existing.division = division
                    updated = True
                if updated:
                    results_updated += 1
            else:
                model_cls = HSResult if source_type == 'hs' else CollegeResult
                result = model_cls(**result_data)
                db.session.add(result)
                existing_lookup[result_key] = result  # Add to lookup to prevent duplicates in same batch
                results_added += 1

        # Commit in batches and give Neon a rest
        if (idx + 1) % batch_size == 0:
            db.session.commit()
            logger.info(f"Progress: {idx + 1}/{total_rows} ({(idx+1)/total_rows*100:.1f}%) - Added {results_added} results")
            time.sleep(0.5)  # Give Neon 500ms rest between batches

    # Final commit
    db.session.commit()
    logger.info(f"✓ Stored {results_added} new results, backfilled {results_updated} existing results")


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
        schools_df = school_scraper.scrape_schools(source_type='hs')

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
        rosters_df = roster_scraper.scrape_all_rosters(schools_df, seasons, source_type='hs')

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

    Args:
        limit_schools: Max schools to scrape (None = all)
        limit_sailors: Max sailors to scrape results for (None = all)
    """
    from app import app

    with app.app_context():
        logger.info("="*70)
        logger.info("  STEP 1: Scraping COLLEGE Schools")
        logger.info("="*70)

        # Use user's school_scraper with college source
        schools_df = school_scraper.scrape_schools(source_type='college')

        if schools_df.empty:
            logger.error("No schools found")
            return

        schools_df = school_scraper.verify_url_slugs(schools_df)

        if limit_schools:
            schools_df = schools_df.head(limit_schools)
            logger.info(f"Limited to {limit_schools} schools")

        # Store schools in database
        store_schools_in_db(schools_df, 'college')

        logger.info("="*70)
        logger.info("  STEP 2: Scraping COLLEGE Rosters")
        logger.info("="*70)

        # Use user's roster_scraper with college source
        seasons = ["f25", "s25", "f24", "s24", "f23", "s23", "f22", "s22"]
        rosters_df = roster_scraper.scrape_all_rosters(schools_df, seasons, source_type='college')

        if rosters_df.empty:
            logger.error("No rosters found")
            return

        # Store sailors in database
        store_sailors_in_db(rosters_df, 'college')

        logger.info("="*70)
        logger.info("  STEP 3: Scraping Individual Sailor Results")
        logger.info("="*70)

        if limit_sailors:
            rosters_df = rosters_df.head(limit_sailors)
            logger.info(f"Limited to {limit_sailors} sailors")

        results_df = scraper_v2.scrape_batch_sailors(rosters_df, delay=0.5, save_interval=50)

        if not results_df.empty:
            store_results_in_db(results_df, 'college')

        logger.info("="*70)
        logger.info("  COLLEGE SCRAPING COMPLETE")
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
        _attach_db_log_handler()
        logger.info("="*70)
        logger.info(f"  STEP 1: Scraping {source_type.upper()} Schools")
        logger.info("="*70)

        # Use user's school_scraper with source_type parameter
        schools_df = school_scraper.scrape_schools(source_type=source_type)

        if schools_df.empty:
            logger.error("No schools found")
            return 0

        schools_df = school_scraper.verify_url_slugs(schools_df)
        _db_log(f"Step 1 ({source_type.upper()}): school list scrape finished — {len(schools_df)} schools",
                step='schools', section='scrape', source=source_type)

        # Store schools in database
        store_schools_in_db(schools_df, source_type)
        _db_log(f"Step 1 ({source_type.upper()}): school store finished — {len(schools_df)} schools",
                step='schools', section='store', source=source_type)

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
        _attach_db_log_handler()
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
        rosters_df = roster_scraper.scrape_all_rosters(schools_df, seasons=[season_code], source_type=source_type)

        if rosters_df.empty:
            logger.warning(f"No rosters found for season {season_code}")
            return 0

        _db_log(f"Step 2 ({source_type.upper()} {season_code.upper()}): roster scrape finished — {len(rosters_df)} sailor rows",
                step='rosters', section='scrape', season=season_code, source=source_type)

        # Store sailors in database
        store_sailors_in_db(rosters_df, source_type)
        _db_log(f"Step 2 ({source_type.upper()} {season_code.upper()}): sailor store finished — {len(rosters_df)} sailor rows",
                step='rosters', section='store', season=season_code, source=source_type)

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
        _attach_db_log_handler()
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

        _db_log(f"Step 3 ({source_type.upper()} {season_code.upper()}): results scrape finished — {len(results_df)} result rows",
                step='results', section='scrape', season=season_code, source=source_type)

        # Store results in database
        store_results_in_db(results_df, source_type)
        _db_log(f"Step 3 ({source_type.upper()} {season_code.upper()}): results store finished — {len(results_df)} result rows",
                step='results', section='store', season=season_code, source=source_type)

        logger.info(f"✓ Stored {len(results_df)} results for {season_code}")
        return len(results_df)


if __name__ == '__main__':
    # Test with limited scope
    run_full_hs_scraper(limit_schools=2, limit_sailors=10)
