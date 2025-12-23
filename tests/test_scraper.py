"""
Unit tests for scraper module.
"""
import sys
from pathlib import Path
import pandas as pd

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper import build_sailor_url, expand_result_fields


class TestBuildSailorUrl:
    """Test URL building for sailor lookups"""

    def test_simple_name(self):
        url = build_sailor_url("John Doe", "https://example.com/sailors/")
        assert url == "https://example.com/sailors/john-doe/"

    def test_name_with_extra_spaces(self):
        url = build_sailor_url("  John  Doe  ", "https://example.com/sailors/")
        assert url == "https://example.com/sailors/john-doe/"

    def test_name_with_multiple_words(self):
        url = build_sailor_url("Mary Jane Smith", "https://example.com/sailors/")
        assert url == "https://example.com/sailors/mary-jane-smith/"

    def test_uppercase_conversion(self):
        url = build_sailor_url("JOHN DOE", "https://example.com/sailors/")
        assert url == "https://example.com/sailors/john-doe/"


class TestExpandResultFields:
    """Test result field expansion"""

    def test_expand_valid_result(self):
        df = pd.DataFrame({
            'Result': ['1/10', '2/15', '3/20']
        })
        result = expand_result_fields(df)

        assert 'Place' in result.columns
        assert 'Total' in result.columns
        assert result['Place'].iloc[0] == '1'
        assert result['Total'].iloc[0] == '10'

    def test_expand_invalid_result(self):
        df = pd.DataFrame({
            'Result': ['DNS', 'DNF', 'N/A']
        })
        result = expand_result_fields(df)

        # Should handle gracefully - non-matching values will be NaN
        assert 'Place' in result.columns
        assert 'Total' in result.columns

    def test_expand_empty_dataframe(self):
        df = pd.DataFrame({
            'Result': []
        })
        result = expand_result_fields(df)

        assert len(result) == 0
        assert 'Place' in result.columns
        assert 'Total' in result.columns
