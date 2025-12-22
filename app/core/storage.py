# app\core\storage.py
"""
Data loading and persistence layer for configuration files.
"""

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.models import ObRule, OnCallRule, Person, Rotation, Settings, ShiftType, TaxBracket

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """General error type for problems loading data files."""

    pass


def _load_json(file_path: Path) -> list[Any] | dict[str, Any]:
    """
    Read and parse JSON with robust error handling.
    Args:
        file_path: Path to the JSON file
    Returns:
        Parsed JSON data as list or dict
    Raises:
        StorageError: If file cannot be read or JSON is invalid
    """
    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.exception("Failed to read JSON file %s", file_path)
        raise StorageError(f"Could not read JSON file {file_path}: {e}") from e

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.exception("Invalid JSON in file %s", file_path)
        raise StorageError(f"Invalid JSON in file {file_path}: {e}") from e


def load_shift_types() -> list[ShiftType]:
    """
    Load shift type definitions from data file.
    Returns:
        List of shift types
    Raises:
        StorageError: If file cannot be loaded or parsed
    """
    file_path = Path("data/shift_types.json")
    data = _load_json(file_path)
    try:
        if not isinstance(data, list):
            raise TypeError("Expected list of shift types")
        shift_types = [ShiftType(**item) for item in data]
    except (TypeError, ValidationError) as e:
        logger.exception("Failed to parse shift types from %s", file_path)
        raise StorageError(f"Could not parse shift types from {file_path}: {e}") from e
    return shift_types


def load_rotation() -> Rotation:
    """
    Load rotation configuration from data file.
    Returns:
        Rotation configuration
    Raises:
        StorageError: If file cannot be loaded or parsed
    """
    file_path = Path("data/rotation.json")
    data = _load_json(file_path)
    try:
        if not isinstance(data, dict):
            raise TypeError("Expected rotation configuration dict")
        rotation = Rotation(**data)
    except (TypeError, ValidationError) as e:
        logger.exception("Failed to parse rotation from %s", file_path)
        raise StorageError(f"Could not parse rotation from {file_path}: {e}") from e
    return rotation


def load_settings() -> Settings:
    """
    Load application settings from data file.
    Returns:
        Application settings
    Raises:
        StorageError: If file cannot be loaded or parsed
    """
    file_path = Path("data/settings.json")
    data = _load_json(file_path)
    try:
        if not isinstance(data, dict):
            raise TypeError("Expected settings dict")
        settings = Settings(**data)
    except (TypeError, ValidationError) as e:
        logger.exception("Failed to parse settings from %s", file_path)
        raise StorageError(f"Could not parse settings from {file_path}: {e}") from e
    return settings


def load_ob_rules() -> list[ObRule]:
    """
    Load OB (unsocial hours) rules from data file.
    Returns:
        List of OB rules
    Raises:
        StorageError: If file cannot be loaded or parsed
    """
    file_path = Path("data/ob_rules.json")
    data = _load_json(file_path)
    try:
        if not isinstance(data, list):
            raise TypeError("Expected list of OB rules")
        ob_rules = [ObRule(**item) for item in data]
    except (TypeError, ValidationError) as e:
        logger.exception("Failed to parse OB rules from %s", file_path)
        raise StorageError(f"Could not parse OB rules from {file_path}: {e}") from e
    return ob_rules


def load_oncall_rules() -> list[OnCallRule]:
    """
    Load on-call compensation rules from data file.
    Returns:
        List of on-call rules
    Raises:
        StorageError: If file cannot be loaded or parsed
    """
    file_path = Path("data/oncall_rules.json")
    data = _load_json(file_path)
    try:
        if not isinstance(data, list):
            raise TypeError("Expected list of on-call rules")
        oncall_rules = [OnCallRule(**item) for item in data]
    except (TypeError, ValidationError) as e:
        logger.exception("Failed to parse on-call rules from %s", file_path)
        raise StorageError(f"Could not parse on-call rules from {file_path}: {e}") from e
    return oncall_rules


def load_tax_brackets() -> list[TaxBracket]:
    """
    Load tax bracket definitions from data file.
    Returns:
        List of tax brackets
    Raises:
        StorageError: If file cannot be loaded or parsed
    """
    file_path = Path("data/tax_brackets.json")
    data = _load_json(file_path)
    try:
        if not isinstance(data, list):
            raise TypeError("Expected list of tax brackets")
        tax_brackets = [TaxBracket(**item) for item in data]
    except (TypeError, ValidationError) as e:
        logger.exception("Failed to parse tax brackets from %s", file_path)
        raise StorageError(f"Could not parse tax brackets from {file_path}: {e}") from e
    return tax_brackets


def calculate_tax_bracket(income: float, tax_brackets: list[TaxBracket]) -> float:
    """
    Calculate the appropriate tax rate for a given income.
    Args:
        income: Income amount
        tax_brackets: List of tax brackets to check against
    Returns:
        Preliminary tax rate as a decimal (0.0 if no bracket matches)
    """
    for bracket in tax_brackets:
        if bracket.lon_till is None or income <= bracket.lon_till:
            return bracket.prel_skatt
    return 0.0  # Default if no bracket matches


def load_persons() -> list[Person]:
    """
    Load person definitions from data file.
    Returns:
        List of persons
    Raises:
        StorageError: If file cannot be loaded or parsed
    """
    file_path = Path("data/persons.json")
    data = _load_json(file_path)
    try:
        if not isinstance(data, list):
            raise TypeError("Expected list of persons")
        persons = [Person(**item) for item in data]
    except (TypeError, ValidationError) as e:
        logger.exception("Failed to parse persons from %s", file_path)
        raise StorageError(f"Could not parse persons from {file_path}: {e}") from e
    return persons


# Cache for tax table data
_tax_table_cache = None


def load_tax_table() -> dict[str, list[dict]]:
    """
    Load Swedish tax table from CSV file.

    Returns:
        Dict with table numbers as keys, each containing list of income brackets:
        {
            "29": [{"from": 1, "to": 2000, "tax": 0}, ...],
            "30": [...],
            ...
        }

    Raises:
        StorageError: If file cannot be loaded or parsed
    """
    global _tax_table_cache

    if _tax_table_cache is not None:
        return _tax_table_cache

    file_path = Path("data/skattetabell.csv")

    if not file_path.exists():
        logger.error("Tax table file not found at %s", file_path)
        raise StorageError(f"Tax table file not found: {file_path}")

    try:
        import csv

        tax_tables = {}

        with open(file_path, encoding="utf-8") as f:
            # Skip BOM if present
            content = f.read()
            if content.startswith("\ufeff"):
                content = content[1:]

            lines = content.splitlines()
            reader = csv.DictReader(lines, delimiter=";")

            for row in reader:
                # Extract relevant fields
                table_nr = row.get("Tabellnr", "").strip()
                income_from = row.get("Inkomst fr.o.m.", "").strip()
                income_to = row.get("Inkomst t.o.m.", "").strip()
                tax_col1 = row.get("Kolumn 1", "").strip()

                # Skip invalid rows
                if not table_nr or not income_from or not tax_col1:
                    continue

                # Parse values
                try:
                    income_from_val = int(income_from)
                    income_to_val = int(income_to) if income_to else None
                    tax_val = int(tax_col1)
                except (ValueError, TypeError):
                    continue

                # Initialize table if not exists
                if table_nr not in tax_tables:
                    tax_tables[table_nr] = []

                # Add bracket
                tax_tables[table_nr].append({"from": income_from_val, "to": income_to_val, "tax": tax_val})

        # Sort each table by income_from
        for table_nr in tax_tables:
            tax_tables[table_nr].sort(key=lambda x: x["from"])

        _tax_table_cache = tax_tables
        logger.info("Loaded tax tables for %d table numbers", len(tax_tables))
        return tax_tables

    except Exception as e:
        logger.exception("Failed to load tax table from %s", file_path)
        raise StorageError(f"Could not load tax table from {file_path}: {e}") from e


def calculate_tax_from_table(income: float, table_number: str) -> float:
    """
    Calculate tax amount from Swedish tax table.

    Args:
        income: Monthly gross income in SEK
        table_number: Tax table number (e.g., "29", "30", "33")

    Returns:
        Tax amount in SEK

    Raises:
        StorageError: If tax table cannot be loaded
        ValueError: If table number is invalid
    """
    if income <= 0:
        return 0.0

    tax_tables = load_tax_table()

    if table_number not in tax_tables:
        available = ", ".join(sorted(tax_tables.keys()))
        raise ValueError(f"Invalid tax table number '{table_number}'. Available: {available}")

    brackets = tax_tables[table_number]

    # Find matching bracket
    for bracket in brackets:
        if bracket["to"] is None:
            # Last bracket (no upper limit)
            if income >= bracket["from"]:
                return float(bracket["tax"])
        else:
            # Normal bracket
            if bracket["from"] <= income <= bracket["to"]:
                return float(bracket["tax"])

    # If income is below first bracket, return 0
    if income < brackets[0]["from"]:
        return 0.0

    # If income is above all brackets, use the last bracket's tax
    return float(brackets[-1]["tax"])


def get_available_tax_tables() -> list[str]:
    """
    Get list of available tax table numbers.

    Returns:
        Sorted list of table numbers (e.g., ["29", "30", "31", "32", "33"])

    Raises:
        StorageError: If tax table cannot be loaded
    """
    tax_tables = load_tax_table()
    return sorted(tax_tables.keys())
