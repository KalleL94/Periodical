import json
from pathlib import Path
from .models import ShiftType, Rotation, Settings

def load_shift_types():
    print("Loading shift types...")
    file_path = Path("data/shift_types.json")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    shift_types = [ShiftType(**item) for item in data]
    print("...done")
    return shift_types

def load_rotation():
    print("Loading rotation...")
    file_path = Path("data/rotation.json")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    rotation = Rotation(**data)
    print("...done")
    return rotation

def load_settings():
    print("Loading settings...")
    file_path = Path("data/settings.json")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    settings = Settings(**data)
    print("...done")
    return settings
