#!/usr/bin/env python3
"""
Test the report.json file to validate structure and content.
"""

import json
import pytest
from pathlib import Path

REPORT_FILE = "report.json"

@pytest.fixture
def report_data():
    """Load the report.json file."""
    report_path = Path(REPORT_FILE)
    assert report_path.exists(), f"{REPORT_FILE} does not exist"
    
    with open(report_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    return data

def test_report_is_list(report_data):
    """Test that report is a list."""
    assert isinstance(report_data, list), "Report should be a list"

def test_report_has_five_entries(report_data):
    """Test that report has exactly 5 entries."""
    assert len(report_data) == 5, f"Expected 5 entries, got {len(report_data)}"

def test_each_entry_has_required_keys(report_data):
    """Test that each entry has the required keys."""
    required_keys = {"name", "stars", "readme_snippet"}
    
    for i, entry in enumerate(report_data):
        assert isinstance(entry, dict), f"Entry {i} is not a dict"
        assert required_keys.issubset(entry.keys()), \
            f"Entry {i} missing required keys. Has: {entry.keys()}, needs: {required_keys}"

def test_each_entry_name_is_valid_string(report_data):
    """Test that each entry has a valid non-empty name."""
    for i, entry in enumerate(report_data):
        assert "name" in entry, f"Entry {i} missing 'name' key"
        assert isinstance(entry["name"], str), f"Entry {i} name is not a string"
        assert len(entry["name"]) > 0, f"Entry {i} name is empty"

def test_each_entry_stars_is_valid_integer(report_data):
    """Test that each entry has valid stars (integer > 0)."""
    for i, entry in enumerate(report_data):
        assert "stars" in entry, f"Entry {i} missing 'stars' key"
        assert isinstance(entry["stars"], int), f"Entry {i} stars is not an integer"
        assert entry["stars"] > 0, f"Entry {i} stars is not > 0, got {entry['stars']}"

def test_each_entry_readme_snippet_is_string(report_data):
    """Test that each entry has a valid readme_snippet."""
    for i, entry in enumerate(report_data):
        assert "readme_snippet" in entry, f"Entry {i} missing 'readme_snippet' key"
        assert isinstance(entry["readme_snippet"], str), f"Entry {i} readme_snippet is not a string"

def test_report_content_summary(report_data):
    """Print a summary of the report."""
    print("\n=== Report Summary ===")
    for entry in report_data:
        snippet = entry.get("readme_snippet", "")[:50] + "..." if entry.get("readme_snippet") else ""
        print(f"  {entry['name']}: {entry['stars']} stars | {snippet}")
