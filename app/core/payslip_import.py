"""Parse Swedish payslip PDFs (lönespecifikationer) into structured rows.

One parser, one categorizer. The PDF text is extracted in layout mode, which keeps
the payslip's columns aligned, so a row can be split on runs of two or more spaces
(single spaces are thousand separators inside Swedish numbers).

Row layout on the payslip:

    Benämning | Från datum | Till datum | Antal | Enhet | Belopp (á-pris) | Summa

Everything except the label and the sum can be missing.
"""

from __future__ import annotations

import datetime
import io
import re
from dataclasses import dataclass

from pypdf import PdfReader

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUM_RE = re.compile(r"^-?\d[\d ]*(?:,\d+)?$")
_FACTOR_RE = re.compile(r"faktor\s+(\d+,\d+)")

# Rounding slack when matching an á-price against a configured rate. The payslip prints öre,
# so a stored rate can sit half an öre off, and float comparison needs a little air on top.
_PRICE_TOLERANCE = 0.015

# On-call rate keys (see rates.DEFAULT_ONCALL_RATES) -> category. Same mapping as
# routes/schedule_personal.py uses when it aggregates on-call pay.
_ONCALL_CATEGORIES: dict[str, str] = {
    "OC_WEEKDAY": "oc_vardag",
    "OC_WEEKEND": "oc_helg",
    "OC_WEEKEND_SAT": "oc_helg",
    "OC_WEEKEND_SUN": "oc_helg",
    "OC_WEEKEND_MON": "oc_helg",
    "OC_HOLIDAY": "oc_helgdag",
    "OC_HOLIDAY_EVE": "oc_helgdag",
    "OC_NATIONALDAGEN": "oc_helgdag",
    "OC_SPECIAL": "oc_storhelg",
}

# Last resort á-price -> category, used when no rates were supplied or they did not match.
# These are one employer's rates and change with every wage revision, so treat them as a
# hint only. Wrong guesses are worse than "unknown", keep the table tight.
_PRICE_HINTS: dict[float, str] = {
    25.52: "OB1",
    38.28: "OB2",
    51.03: "OB3",
    99.94: "OB5",
    75.00: "oc_vardag",
    97.00: "oc_helg",
}

# "Faktor 1,24" style labels: the factor is the hourly multiplier, which is stable across
# wage revisions even though the á-price is not. OB3 and OB4 share the same factor in this
# agreement, so 1,24 cannot be told apart and resolves to OB3.
_FACTOR_CATEGORIES: dict[str, str] = {
    "1,12": "OB1",
    "1,18": "OB2",
    "1,24": "OB3",
    "1,47": "OB5",
}


@dataclass(frozen=True)
class Row:
    label: str
    from_date: datetime.date | None
    to_date: datetime.date | None
    qty: float | None
    unit: str | None
    unit_price: float | None
    amount: float
    category: str


def _num(token: str) -> float | None:
    """Parse a Swedish number ("-1 366,15") into a float."""
    if not _NUM_RE.match(token.strip()):
        return None
    return float(token.replace(" ", "").replace(",", "."))


def _find_amount(text: str, label: str) -> float | None:
    """Return the first number following a case-sensitive footer label."""
    match = re.search(re.escape(label) + r"[^\n\d-]*(-?\d[\d ]*,\d{2})", text)
    return _num(match.group(1)) if match else None


def _rate_category(unit_price: float, rates: dict) -> str | None:
    """Match an á-price against a user's own rates, as returned by rates.get_user_rates()."""
    pairs = [(code, rate) for code, rate in (rates.get("ob") or {}).items()]
    pairs += [(_ONCALL_CATEGORIES[k], v) for k, v in (rates.get("oncall") or {}).items() if k in _ONCALL_CATEGORIES]
    if rates.get("ot"):
        pairs.append(("ot", rates["ot"]))

    matches = sorted({code for code, rate in pairs if rate and abs(float(rate) - unit_price) <= _PRICE_TOLERANCE})
    # Rates are not unique (OB4 is commonly configured with the same rate as OB3), so a price
    # can fit several categories. Sorting and taking the first picks the lowest OB level every
    # time: deterministic and conservative beats guessing at the higher one.
    return matches[0] if matches else None


def categorize(label: str, unit_price: float | None = None, rates: dict | None = None) -> str:
    """Map a payslip label to the pay category keys the app uses.

    Labels drift between months for the same compensation ("OB tillägg helg" became
    "Faktor 1,24"), so match on keywords first, then on the á-price. Order: keywords,
    "Faktor X,YZ", the user's own rates, the built-in price hints. Anything unrecognised
    becomes "unknown": a visible gap in the UI beats a silent mis-match.
    """
    text = label.casefold()

    # Order matters: the specific compounds all contain "månadslön".
    if "karensavdrag" in text:
        return "karens"
    if "sjukavdrag" in text:
        return "sick_deduction"
    if "sjuklön" in text:
        return "sick_pay"
    if "semester" in text:
        return "vacation_deduction" if "avdrag" in text else "vacation_pay"
    if "övertid" in text or "mertid" in text or "jour" in text:
        return "ot"
    if "beredskap" in text:
        if "storhelg" in text:
            return "oc_storhelg"
        if "helgdag" in text:
            return "oc_helgdag"
        return "oc_helg" if "helg" in text else "oc_vardag"

    factor = _FACTOR_RE.search(text)
    if factor:
        return _FACTOR_CATEGORIES.get(factor.group(1), "unknown")
    if text.startswith("ob"):
        if "storhelg" in text:
            return "OB5"
        if "helgdag" in text:
            return "OB4"
        if "helg" in text:
            return "OB3"
        if "natt" in text:
            return "OB2"
        if "kväll" in text:
            return "OB1"

    if "utlägg" in text or "friskvård" in text:
        return "expense"
    if text == "månadslön":
        return "base"
    if text == "timlön" or "arbetad" in text:
        return "norm"

    if unit_price:
        price = abs(unit_price)
        if rates:
            matched = _rate_category(price, rates)
            if matched:
                return matched
        return _PRICE_HINTS.get(round(price, 2), "unknown")
    return "unknown"


def _parse_row(line: str, rates: dict | None = None) -> Row | None:
    """Parse one payslip line. Returns None for anything that is not a pay row."""
    dates = [datetime.date.fromisoformat(d) for d in _DATE_RE.findall(line)]
    parts = [p.strip() for p in re.split(r"\s{2,}", _DATE_RE.sub(" ", line).strip()) if p.strip()]
    if len(parts) < 2:
        return None

    label, tokens = parts[0], parts[1:]
    amount = _num(tokens[-1])
    if amount is None or _num(label) is not None:
        return None

    # Middle columns: quantity sits before the unit, á-price after it.
    middle = tokens[:-1]
    unit = next((t for t in middle if _num(t) is None), None)
    qty = unit_price = None
    if unit is not None:
        i = middle.index(unit)
        qty = _num(middle[i - 1]) if i > 0 else None
        unit_price = _num(middle[i + 1]) if i + 1 < len(middle) else None

    return Row(
        label=label,
        from_date=dates[0] if dates else None,
        to_date=dates[1] if len(dates) > 1 else (dates[0] if dates else None),
        qty=qty,
        unit=unit,
        unit_price=unit_price,
        amount=amount,
        category=categorize(label, unit_price, rates),
    )


def _page_rows(text: str, rates: dict | None = None) -> list[Row]:
    """Parse the rows between the column header and the summary footer of one page."""
    lines = text.splitlines()
    start = next((i + 1 for i, ln in enumerate(lines) if "Benämning" in ln and "Summa" in ln), 0)
    end = next((i for i, ln in enumerate(lines) if "Skattetabell" in ln), len(lines))
    rows = (_parse_row(ln, rates) for ln in lines[start:end] if ln.strip())
    return [row for row in rows if row is not None]


def parse_payslip_pdf(data: bytes, rates: dict | None = None) -> dict:
    """Parse a Swedish payslip PDF.

    Args:
        data: the PDF bytes.
        rates: the payslip owner's rates as returned by rates.get_user_rates(), used to
            categorize rows whose label is unrecognised. Rates differ per user, so passing
            them beats the built-in price hints.

    Returns {"period": (year, month) | None, "rows": [Row, ...], "gross": float | None,
             "tax": float | None, "net": float | None, "tax_table": str | None}.
    Tax is negative, matching how the payslip prints it.
    """
    pages = [
        (page.extract_text(extraction_mode="layout") or "").replace("\u00a0", " ")
        for page in PdfReader(io.BytesIO(data)).pages
    ]
    text = "\n".join(pages)

    period = None
    earned = re.search(r"Intjänandeperiod\s+(\d{4})-(\d{2})", text)
    if earned:
        period = (int(earned.group(1)), int(earned.group(2)))

    # The net amount is printed under the "ATT UTBETALA" heading, right-aligned.
    net = None
    payout = re.search(r"ATT UTBETALA[^\n]*\n([^\n]*)", text)
    if payout:
        numbers = re.findall(r"-?\d[\d ]*,\d{2}", payout.group(1))
        net = _num(numbers[-1]) if numbers else None

    table = re.search(r"Skattetabell\s+(\S+)", text)

    return {
        "period": period,
        "rows": [row for page in pages for row in _page_rows(page, rates)],
        "gross": _find_amount(text, "Bruttolön"),
        "tax": _find_amount(text, "Preliminärskatt"),
        "net": net,
        "tax_table": table.group(1) if table else None,
    }
