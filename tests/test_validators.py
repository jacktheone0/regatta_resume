"""
Unit tests for validators module.
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'regatta_resume'))

from validators import (
    validate_sailor_name,
    validate_date,
    validate_date_range,
    validate_max_regattas,
    validate_regatta_filter,
    sanitize_filename,
    validate_edit_payload
)


class TestValidateSailorName:
    """Test sailor name validation"""

    def test_valid_name(self):
        valid, error = validate_sailor_name("Christopher Fulton")
        assert valid is True
        assert error is None

    def test_valid_name_with_hyphen(self):
        valid, error = validate_sailor_name("Mary-Jane Smith")
        assert valid is True
        assert error is None

    def test_valid_name_with_apostrophe(self):
        valid, error = validate_sailor_name("O'Brien")
        assert valid is True
        assert error is None

    def test_empty_name(self):
        valid, error = validate_sailor_name("")
        assert valid is False
        assert "empty" in error.lower()

    def test_too_short(self):
        valid, error = validate_sailor_name("A")
        assert valid is False
        assert "at least 2" in error

    def test_too_long(self):
        valid, error = validate_sailor_name("A" * 101)
        assert valid is False
        assert "less than 100" in error

    def test_invalid_characters(self):
        valid, error = validate_sailor_name("John@Doe")
        assert valid is False
        assert "invalid characters" in error.lower()


class TestValidateDate:
    """Test date validation"""

    def test_valid_date(self):
        valid, error = validate_date("2024-01-15")
        assert valid is True
        assert error is None

    def test_empty_date(self):
        valid, error = validate_date("")
        assert valid is True  # Optional field
        assert error is None

    def test_invalid_format(self):
        valid, error = validate_date("01/15/2024")
        assert valid is False
        assert "format" in error.lower()

    def test_invalid_date(self):
        valid, error = validate_date("2024-02-30")
        assert valid is False
        assert "invalid" in error.lower()


class TestValidateDateRange:
    """Test date range validation"""

    def test_valid_range(self):
        valid, error = validate_date_range("2024-01-01", "2024-12-31")
        assert valid is True
        assert error is None

    def test_empty_range(self):
        valid, error = validate_date_range("", "")
        assert valid is True
        assert error is None

    def test_reversed_range(self):
        valid, error = validate_date_range("2024-12-31", "2024-01-01")
        assert valid is False
        assert "before" in error.lower()


class TestValidateMaxRegattas:
    """Test max regattas validation"""

    def test_valid_number(self):
        valid, error = validate_max_regattas("100")
        assert valid is True
        assert error is None

    def test_empty(self):
        valid, error = validate_max_regattas("")
        assert valid is True  # Optional, uses default
        assert error is None

    def test_too_small(self):
        valid, error = validate_max_regattas("0")
        assert valid is False
        assert "at least 1" in error

    def test_too_large(self):
        valid, error = validate_max_regattas("1001")
        assert valid is False
        assert "cannot exceed" in error

    def test_non_numeric(self):
        valid, error = validate_max_regattas("abc")
        assert valid is False
        assert "valid number" in error


class TestValidateRegattaFilter:
    """Test regatta filter validation"""

    def test_valid_filter(self):
        valid, error = validate_regatta_filter("Championship")
        assert valid is True
        assert error is None

    def test_empty_filter(self):
        valid, error = validate_regatta_filter("")
        assert valid is True  # Optional
        assert error is None

    def test_too_long(self):
        valid, error = validate_regatta_filter("A" * 101)
        assert valid is False
        assert "less than 100" in error

    def test_invalid_characters(self):
        valid, error = validate_regatta_filter("Test<script>")
        assert valid is False
        assert "invalid characters" in error.lower()


class TestSanitizeFilename:
    """Test filename sanitization"""

    def test_normal_filename(self):
        result = sanitize_filename("resume.pdf")
        assert result == "resume.pdf"

    def test_path_traversal(self):
        result = sanitize_filename("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result
        assert "\\" not in result

    def test_special_characters(self):
        result = sanitize_filename("my resume<test>.pdf")
        assert "<" not in result
        assert ">" not in result

    def test_empty_filename(self):
        result = sanitize_filename("")
        assert result == "unnamed"


class TestValidateEditPayload:
    """Test edit payload validation"""

    def test_valid_payload(self):
        payload = {
            "edits": [
                {"row": 0, "field": "Regatta", "value": "Test Regatta"}
            ]
        }
        valid, error = validate_edit_payload(payload)
        assert valid is True
        assert error is None

    def test_missing_edits(self):
        payload = {}
        valid, error = validate_edit_payload(payload)
        assert valid is False
        assert "edits" in error.lower()

    def test_invalid_field(self):
        payload = {
            "edits": [
                {"row": 0, "field": "InvalidField", "value": "test"}
            ]
        }
        valid, error = validate_edit_payload(payload)
        assert valid is False
        assert "invalid field" in error.lower()

    def test_too_many_edits(self):
        payload = {
            "edits": [{"row": i, "field": "Regatta", "value": "test"} for i in range(1001)]
        }
        valid, error = validate_edit_payload(payload)
        assert valid is False
        assert "too many" in error.lower()

    def test_value_too_long(self):
        payload = {
            "edits": [
                {"row": 0, "field": "Regatta", "value": "A" * 501}
            ]
        }
        valid, error = validate_edit_payload(payload)
        assert valid is False
        assert "too long" in error.lower()
