"""
Schedule module - schemahantering och löneberäkningar.

Exporterar alla publika funktioner för bakåtkompatibilitet.
"""

from app.core.storage import load_ob_rules, load_persons, load_settings, load_tax_brackets

from .core import (
    calculate_shift_hours,
    clear_schedule_cache,
    determine_shift_for_date,
    get_rotation,
    get_rotation_era_for_date,
    get_rotation_length_for_date,
    get_rotation_start_date,
    get_settings,
    get_shift_types,
    get_vacation_shift,
    weekday_names,
)
from .cowork import build_cowork_details, build_cowork_stats
from .holidays_ob import build_special_ob_rules_for_year
from .ob import (
    calculate_ob_hours,
    calculate_ob_pay,
    get_combined_rules_for_year,
    get_ob_rules,
    get_special_rules_for_year,
    select_ob_rules_for_date,
)
from .overtime import (
    calculate_overtime_pay,
    get_overtime_shift_for_date,
    get_overtime_shifts_for_month,
)
from .period import (
    build_week_data,
    generate_month_data,
    generate_period_data,
    generate_year_data,
)
from .summary import (
    summarize_month_for_person,
    summarize_year_by_month,
    summarize_year_for_person,
)
from .vacation import get_vacation_dates_for_year
from .wages import (
    add_new_wage,
    get_all_user_wages,
    get_current_wage_record,
    get_user_wage,
    get_wage_history,
)

# === Bakåtkompatibilitet (deprecated - använd funktionerna istället) ===
settings = load_settings()
tax_brackets = load_tax_brackets()
ob_rules = load_ob_rules()
persons = load_persons()
rotation_start_date = get_rotation_start_date()
rotation = get_rotation()

# Alias för gamla funktionsnamn
_cached_special_rules = get_special_rules_for_year
_select_ob_rules_for_date = select_ob_rules_for_date

__all__ = [
    # core
    "determine_shift_for_date",
    "calculate_shift_hours",
    "get_shift_types",
    "get_rotation",
    "get_settings",
    "get_rotation_start_date",
    "get_rotation_era_for_date",
    "get_rotation_length_for_date",
    "get_vacation_shift",
    "weekday_names",
    "clear_schedule_cache",
    # ob
    "calculate_ob_hours",
    "calculate_ob_pay",
    "get_ob_rules",
    "get_special_rules_for_year",
    "get_combined_rules_for_year",
    "build_special_ob_rules_for_year",
    "select_ob_rules_for_date",
    # overtime
    "calculate_overtime_pay",
    "get_overtime_shift_for_date",
    "get_overtime_shifts_for_month",
    # wages
    "get_user_wage",
    "get_all_user_wages",
    "add_new_wage",
    "get_wage_history",
    "get_current_wage_record",
    # vacation
    "get_vacation_dates_for_year",
    # period
    "build_week_data",
    "generate_period_data",
    "generate_year_data",
    "generate_month_data",
    # summary
    "summarize_month_for_person",
    "summarize_year_for_person",
    "summarize_year_by_month",
    # cowork
    "build_cowork_stats",
    "build_cowork_details",
    # Bakåtkompatibilitet (deprecated)
    "settings",
    "tax_brackets",
    "ob_rules",
    "persons",
    "rotationrotation_start_date",
    "_cached_special_rules",
    "_select_ob_rules_for_date",
]
