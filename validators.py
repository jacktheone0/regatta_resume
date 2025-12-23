"""
Input validation utilities for Regatta Resume Builder.
"""
import re
from datetime import datetime
from typing import Optional, Tuple


def validate_sailor_name(name: str) -> Tuple[bool, Optional[str]]:
    """
    Validate sailor name input.

    Args:
        name: Sailor name to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not name or not name.strip():
        return False, "Sailor name cannot be empty"

    # Remove extra whitespace
    name = name.strip()

    # Length check
    if len(name) < 2:
        return False, "Sailor name must be at least 2 characters"

    if len(name) > 100:
        return False, "Sailor name must be less than 100 characters"

    # Allow letters, spaces, hyphens, apostrophes, and periods
    if not re.match(r"^[a-zA-Z\s\-'.]+$", name):
        return False, "Sailor name contains invalid characters"

    return True, None


def validate_date(date_str: str) -> Tuple[bool, Optional[str]]:
    """
    Validate date string in YYYY-MM-DD format.

    Args:
        date_str: Date string to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not date_str or not date_str.strip():
        return True, None  # Optional field

    date_str = date_str.strip()

    # Check format
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return False, "Date must be in YYYY-MM-DD format"

    # Try parsing
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True, None
    except ValueError:
        return False, "Invalid date"


def validate_date_range(start_date: str, end_date: str) -> Tuple[bool, Optional[str]]:
    """
    Validate date range.

    Args:
        start_date: Start date string
        end_date: End date string

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Validate individual dates
    start_valid, start_error = validate_date(start_date)
    if not start_valid:
        return False, f"Start date error: {start_error}"

    end_valid, end_error = validate_date(end_date)
    if not end_valid:
        return False, f"End date error: {end_error}"

    # If both are provided, check range
    if start_date and end_date:
        try:
            start = datetime.strptime(start_date.strip(), '%Y-%m-%d')
            end = datetime.strptime(end_date.strip(), '%Y-%m-%d')

            if start > end:
                return False, "Start date must be before or equal to end date"

        except ValueError:
            return False, "Invalid date range"

    return True, None


def validate_max_regattas(max_regattas: str) -> Tuple[bool, Optional[str]]:
    """
    Validate max regattas parameter.

    Args:
        max_regattas: Maximum regattas value

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not max_regattas or not max_regattas.strip():
        return True, None  # Optional, will use default

    try:
        value = int(max_regattas.strip())

        if value < 1:
            return False, "Maximum regattas must be at least 1"

        if value > 1000:
            return False, "Maximum regattas cannot exceed 1000"

        return True, None

    except ValueError:
        return False, "Maximum regattas must be a valid number"


def validate_regatta_filter(filter_str: str) -> Tuple[bool, Optional[str]]:
    """
    Validate regatta name filter.

    Args:
        filter_str: Filter string

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not filter_str or not filter_str.strip():
        return True, None  # Optional field

    filter_str = filter_str.strip()

    if len(filter_str) > 100:
        return False, "Filter text must be less than 100 characters"

    # Basic sanitization - no special regex characters that could cause issues
    # Allow alphanumeric, spaces, and common punctuation
    if not re.match(r"^[a-zA-Z0-9\s\-'.&]+$", filter_str):
        return False, "Filter contains invalid characters"

    return True, None


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent path traversal attacks.

    Args:
        filename: Filename to sanitize

    Returns:
        Sanitized filename
    """
    # Remove any directory components
    filename = filename.replace('/', '').replace('\\', '')

    # Remove any null bytes
    filename = filename.replace('\0', '')

    # Remove leading/trailing dots and spaces
    filename = filename.strip('. ')

    # Replace unsafe characters
    filename = re.sub(r'[^\w\-.]', '_', filename)

    # Ensure it's not empty
    if not filename:
        filename = 'unnamed'

    return filename


def validate_edit_payload(payload: dict) -> Tuple[bool, Optional[str]]:
    """
    Validate edit payload structure.

    Args:
        payload: Edit payload dictionary

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(payload, dict):
        return False, "Payload must be a JSON object"

    if 'edits' not in payload:
        return False, "Missing 'edits' field"

    edits = payload['edits']
    if not isinstance(edits, list):
        return False, "'edits' must be an array"

    if len(edits) > 1000:
        return False, "Too many edits (max 1000)"

    # Validate each edit
    allowed_fields = {'Source', 'Regatta', 'Date', 'Place', 'Result', 'Team', 'Years', 'Role', 'Notes'}

    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return False, f"Edit #{i} is not an object"

        if 'row' not in edit:
            return False, f"Edit #{i} missing 'row' field"

        if 'field' not in edit:
            return False, f"Edit #{i} missing 'field' field"

        if 'value' not in edit:
            return False, f"Edit #{i} missing 'value' field"

        # Validate row is numeric
        try:
            row = int(edit['row'])
            if row < 0 or row > 10000:
                return False, f"Edit #{i} has invalid row index"
        except (ValueError, TypeError):
            return False, f"Edit #{i} has non-numeric row"

        # Validate field is allowed
        if edit['field'] not in allowed_fields:
            return False, f"Edit #{i} has invalid field '{edit['field']}'"

        # Validate value length
        value = edit.get('value', '')
        if value and len(str(value)) > 500:
            return False, f"Edit #{i} value too long (max 500 characters)"

    return True, None
