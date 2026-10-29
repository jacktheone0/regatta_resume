"""
Results-table parsing: the part most exposed to ClubSpot changing its page.
Every shape we expect to meet is pinned here so a change shows up as a
failing test rather than silently empty regattas.
"""
from datetime import date

from clubspot_common import (attach_fleet_sizes, count_boats, extract_placement,
                             parse_result_row, parse_results_tables, regatta_status,
                             scoring_code, split_names)


def table(headers, rows):
    return [{'headers': headers, 'rows': rows}]


def test_header_table_with_crew_column_yields_one_row_per_person():
    results = parse_results_tables(table(
        ['Pos', 'Sail', 'Skipper', 'Crew', 'Club', 'Points'],
        [['1', '123', 'Jane Doe', 'Jim Roe', 'SYC', '5.0'],
         ['2', '456', 'Ann Lee', '', 'XYC', '9.0']]))
    assert [(r['sailor_name'], r['role'], r['placement']) for r in results] == [
        ('Jane Doe', 'skipper', 1), ('Jim Roe', 'crew', 1), ('Ann Lee', 'skipper', 2)]
    assert results[0]['crew_partner'] == 'Jim Roe'
    assert results[1]['crew_partner'] == 'Jane Doe'
    assert results[2]['crew_partner'] is None
    assert results[0]['points_scored'] == 5.0
    assert results[0]['team_name'] == 'SYC'
    assert results[0]['sail_number'] == '123'


def test_names_stacked_in_one_cell_are_skipper_then_crew():
    results = parse_results_tables(table(
        ['Place', 'Boat', 'Skipper', 'Total'],
        [['1', 'Hull', 'Jane Doe\nJim Roe\nSam Poe', '3']]))
    assert [(r['sailor_name'], r['role']) for r in results] == [
        ('Jane Doe', 'skipper'), ('Jim Roe', 'crew'), ('Sam Poe', 'crew')]
    assert results[1]['crew_partner'] == 'Jane Doe, Sam Poe'


def test_scoring_code_rows_have_no_place_but_keep_the_code():
    results = parse_results_tables(table(
        ['Pos', 'Skipper', 'Pts'],
        [['1', 'Jane Doe', '1'], ['DNF', 'Jim Roe', '12']]))
    assert results[1]['placement'] is None
    assert results[1]['scoring_code'] == 'DNF'
    assert results[0]['scoring_code'] is None


def test_no_header_falls_back_to_positional_parsing():
    results = parse_results_tables(table([], [['3', '7', 'Jane Doe', '14.0'], ['Pos', 'Sail', 'Name', 'Pts']]))
    assert len(results) == 1
    assert results[0]['sailor_name'] == 'Jane Doe'
    assert results[0]['placement'] == 3
    assert results[0]['points_scored'] == 14.0


def test_identical_rows_rendered_twice_are_parsed_once():
    row = ['1', 'Jane Doe', '2']
    results = parse_results_tables(table(['Pos', 'Skipper', 'Pts'], [row, list(row)]))
    assert len(results) == 1


def test_fleet_size_counts_boats_per_division_not_people():
    results = attach_fleet_sizes(parse_results_tables(table(
        ['Pos', 'Division', 'Skipper', 'Crew'],
        [['1', 'A', 'Jane Doe', 'Jim Roe'],
         ['2', 'A', 'Ann Lee', 'Bob Day'],
         ['3', 'A', 'Cy Fox', ''],
         ['1', 'B', 'Di Gee', 'Ed Hay'],
         ['2', 'B', 'Flo Ivy', '']])))
    assert count_boats(results) == {'A': 3, 'B': 2}
    a_rows = [r for r in results if r['division'] == 'A']
    assert len(a_rows) == 5                      # five people on three boats
    assert {r['fleet_size'] for r in a_rows} == {3}
    assert all(r['fleet_size'] == 2 for r in results if r['division'] == 'B')


def test_header_without_place_or_skipper_column_uses_positional_parser():
    results = parse_results_tables(table(['Club', 'Boat'], [['1', '7', 'Jane Doe', '2.0']]))
    assert results and results[0]['sailor_name'] == 'Jane Doe'


def test_split_names_handles_separators_and_last_first():
    assert split_names('Doe, Jane') == ['Jane Doe']
    assert split_names('Jane Doe / Jim Roe') == ['Jane Doe', 'Jim Roe']
    assert split_names('Jane Doe & Jim Roe') == ['Jane Doe', 'Jim Roe']
    assert split_names('Jane Doe and Jim Roe') == ['Jane Doe', 'Jim Roe']
    assert split_names('TBD') == []
    assert split_names('') == []


def test_place_and_code_extraction():
    assert extract_placement('3rd') == 3
    assert extract_placement('12.') == 12
    assert extract_placement('DNF') is None
    assert scoring_code('dsq') == 'DSQ'
    assert scoring_code('4') is None


def test_legacy_parse_result_row_returns_skipper_only():
    result = parse_result_row('2 | 99 | Jane Doe\nJim Roe | 7.0')
    assert result['sailor_name'] == 'Jane Doe'
    assert result['placement'] == 2
    assert result['crew_partner'] == 'Jim Roe'


def test_regatta_status_from_dates():
    today = date(2026, 10, 29)
    assert regatta_status(date(2026, 11, 2), date(2026, 11, 3), today) == 'upcoming'
    assert regatta_status(date(2026, 10, 1), date(2026, 12, 1), today) == 'in_progress'
    assert regatta_status(date(2026, 10, 1), date(2026, 10, 2), today) == 'final'
    assert regatta_status(date(2026, 10, 1), None, today) == 'final'
    # Placeholder far-future end date on something that started long ago
    assert regatta_status(date(2025, 1, 1), date(2030, 1, 1), today) == 'final'
    assert regatta_status(None, date(2026, 12, 1), today) == 'in_progress'
