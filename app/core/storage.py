import json
from pathlib import Path
from .models import ShiftType, Rotation, Settings, ObRule, TaxBracket, Person

def load_shift_types():
    file_path = Path("data/shift_types.json")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    shift_types = [ShiftType(**item) for item in data]
    return shift_types

def load_rotation():
    file_path = Path("data/rotation.json")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    rotation = Rotation(**data)
    return rotation

def load_settings():
    file_path = Path("data/settings.json")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    settings = Settings(**data)
    return settings

def load_ob_rules():
    file_path = Path("data/ob_rules.json")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    ob_rules = [ObRule(**item) for item in data]
    return ob_rules

def load_tax_brackets():
    file_path = Path("data/tax_brackets.json")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    tax_brackets = [TaxBracket(**item) for item in data]
    return tax_brackets

def calculate_tax_bracket(income: float, tax_brackets: list[TaxBracket]) -> float:
    for bracket in tax_brackets:
        if bracket.lon_till is None or income <= bracket.lon_till:
            return bracket.prel_skatt
    return 0.0  # Default if no bracket matches

def load_persons():
    file_path = Path("data/persons.json")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    persons = [Person(**item) for item in data]
    return persons