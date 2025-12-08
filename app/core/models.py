from pydantic import BaseModel
from typing import Optional, List, Dict

class ShiftType(BaseModel):
    code: str
    label: Optional[str]
    start_time: Optional[str]
    end_time: Optional[str]
    color: Optional[str]

class Rotation(BaseModel):
    rotation_length: int
    weeks: Dict[str, List[str]]
    
class Settings(BaseModel):
    rotation_start_date: str
    monthly_salary: Optional[int]
