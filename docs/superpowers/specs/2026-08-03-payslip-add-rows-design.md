# Payslip: add rows, karens split and vacation supplement payout

Date: 2026-08-03
Branch: `feat/payslip-add-rows`

## Problem

The payslip page (`/month/{id}/payslip`) can only edit rows the app already
computed. `build_payslip_rows` skips rows whose amount rounds to zero
(`payslip.py:186`), so a month where the app computed no OB3 has no OB3 row and
therefore no edit form. The override route also rejects any `row_key` outside
`ROW_ORDER` (`routes/payslip.py:196`), so a pay type the model does not know
cannot be entered at all.

Two pay types are missing entirely: the waiting-day deduction (karens), which is
netted inside the single sick deduction, and the split of the vacation
supplement into its fixed and variable parts. Employers differ on whether the
variable part is paid per taken vacation day or as one lump sum, and the app
only models the first.

Manual rows must also reach the month and year views, not only gross pay.

## Part 1: add a row

A form below the payslip table with a `<select>` over every `ROW_ORDER` key not
already on the slip, labelled from `payslip_row_labels`, plus an "own label"
option that reveals a free-text field. It posts to the existing
`/month/{id}/payslip/override` endpoint.

The only route change: replace the `row_key not in ROW_ORDER` rejection with a
format check (1-40 characters, matching the `row_key` column width). The
`NON_OVERRIDABLE_KEYS` check stays.

No migration. `apply_payslip_overrides` already creates a row for a key the app
did not compute, and the template already falls back to the key as its own
label via `payslip_row_labels.get(row.key, row.key)`.

Free-text keys are per month, like every other override: `PayslipOverride` is
keyed on `(user_id, year, month, row_key)`.

## Part 2: karens as its own row

`absence_details` already carries `karens_hours` per day (`wages.py:558`). Split
the SICK entries in `build_payslip_rows` into two rows:

- `karens`: `karens_hours x hourly wage` (100% deduction)
- `sick_deduction`: the remainder (20% of the non-karens sick hours)

The two sum to today's single deduction, so gross is unchanged and nothing
double counts. `COMPARE_BUCKETS["karens"] = "sick"` already exists, so the
upload comparison is unaffected.

`karens` is added to `ROW_ORDER` (between `sick_deduction` and `vab_deduction`)
and to `payslip_row_labels` in both languages.

## Part 3: manual rows reach month and year

Gross already follows every override (`summary.py:419`). The itemised totals do
not: `_OVERRIDE_TO_TOTAL` only routes the sick and absence rows.

Extend it with:

| Row key | Total field | Sign |
|---|---|---|
| `OB1`-`OB5` | `ob_pay` | +1 |
| `oc_vardag`, `oc_helg`, `oc_helgdag`, `oc_storhelg` | `oncall_pay` | +1 |
| `ot` | `ot_pay` | +1 |
| `substitute` | `substitute_base_pay` | +1 |
| `karens` | `absence_deduction` | -1 |

The per-day breakdown table sums to the computed value, so a moved aggregate no
longer matches the rows beneath it. The per-day rows stay untouched (they show
what the app computed); the aggregate gets a "manual adjustment" marker showing
the difference. `summarize_month_for_person` exposes the deltas as
`totals["override_deltas"]` so the templates can render the marker.

Free-text rows have no totals field and move gross only, which is correct.

## Part 4: vacation supplement, fixed and variable

Three new keys in `DEFAULT_VACATION_RATES` (`rates.py:34`). `custom_rates` is a
JSON dict merged over the defaults, so this needs no migration.

| Key | Default | Meaning |
|---|---|---|
| `fixed_per_day` | `None` | Kronor per day. Empty keeps today's `monthly_salary * fixed_pct`. |
| `variable_payout` | `"per_day"` | `"lump"` pays the variable part as one sum instead. |
| `variable_payout_month` | `None` | Empty means the vacation year's start month (`User.vacation_year_start_month`). |

In `calculate_vacation_supplement` (`vacation.py:625`):

- `fixed_per_day = vac.get("fixed_per_day") or round(monthly_salary * vac["fixed_pct"], 2)`
- when `variable_payout == "lump"`, `variable_per_day` drops out of
  `supplement_per_day`, so a taken vacation day pays the fixed part only. The
  unreduced variable figure is still returned as `variable_per_day` so the
  payout path and the lump row can read it.

The lump is a payslip row `vacation_variable_lump`, added in the payout month
with amount `variable_per_day x entitled_days` (the entitlement, not the days
actually taken).

The computed `vacation_pay` row splits into `vacation_fixed` and
`vacation_variable`, both mapped to the `vacation` compare bucket, both
non-overridable for the same double-counting reason as today: the supplement is
folded into gross outside `summarize_month_for_person`. The fixed amount is
corrected through `fixed_per_day` in the rate settings instead.

**Must be fixed along with this:** saved-day payout (`vacation.py:373` and
`:551`) computes `payout_per_day` from `supplement_per_day`. Under `"lump"` that
figure no longer contains the variable part, so payout would silently shrink.
Both call sites must add the variable part back explicitly.

The admin vacation rate form gets the three fields.

## Testing

- `tests/test_payslip_view.py`: adding a zero row through the form, adding a
  free-text row, rejecting an over-long key, and that a free-text row survives a
  reload.
- `tests/test_payslip.py`: karens splits out of the sick deduction with the sum
  unchanged; the new `_OVERRIDE_TO_TOTAL` entries move their totals.
- New vacation tests: `fixed_per_day` overriding the percentage, `"lump"`
  zeroing the per-day variable part, the lump row landing in the payout month,
  and saved-day payout keeping the variable part under `"lump"`.

## Out of scope

Storing uploaded payslips, mapping unknown parsed rows to categories, and
recurring rows that carry between months.
