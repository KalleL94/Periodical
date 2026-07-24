#!/usr/bin/env python3
"""Generate the anonymised payslip PDF the parser tests run against.

The real payslips live in temp/ (gitignored: they carry a bank account, an
address and an employee number). CI therefore has no test data at all unless a
fixture is committed, and an untested parser is a parser that rots.

This writes a minimal PDF by hand rather than pulling in a PDF-authoring
dependency for one fixture. The layout mirrors a real Agiremus payslip closely
enough that pypdf's layout extraction produces the same column structure, with
every personal detail replaced.

Run once and commit the result:
    venv/bin/python3 tests/fixtures/make_payslip_fixture.py
"""

from pathlib import Path

# (x, y, text). y counts from the page bottom, as PDF user space does.
LINES = [
    (330, 800, "Lönebesked"),
    (330, 786, "Månadslön juni 2026"),
    (330, 750, "Anonym Testsson"),
    (60, 715, "Anställd"),
    (150, 715, "999"),
    (330, 715, "Utbetalningsdag"),
    (450, 715, "2026-07-24"),
    (60, 702, "Bankkonto"),
    (150, 702, "0000-0000000"),
    (330, 702, "Intjänandeperiod"),
    (450, 702, "2026-06-01--2026-06-30"),
    (330, 689, "Avvikelseperiod"),
    (450, 689, "2026-06-01--2026-06-30"),
    # Column headers. The x positions define the columns the parser splits on.
    (60, 650, "Benämning"),
    (240, 650, "Från datum"),
    (320, 650, "Till datum"),
    (405, 650, "Antal"),
    (440, 650, "Enhet"),
    (500, 650, "Belopp"),
    (555, 650, "Summa"),
    # Rows.
    (60, 630, "Månadslön"),
    (440, 630, "mån"),
    (548, 630, "37 000,00"),
    (60, 614, "Övertid betald 100%, timlön"),
    (240, 614, "2026-06-01"),
    (320, 614, "2026-06-30"),
    (400, 614, "8,00"),
    (440, 614, "tim"),
    (497, 614, "422,86"),
    (550, 614, "3 382,88"),
    (60, 598, "Beredskap varrdag 75kr"),
    (240, 598, "2026-06-04"),
    (320, 598, "2026-06-04"),
    (396, 598, "40,00"),
    (440, 598, "tim"),
    (503, 598, "75,00"),
    (550, 598, "3 000,00"),
    (60, 582, "Faktor 1,24"),
    (240, 582, "2026-06-03"),
    (320, 582, "2026-06-03"),
    (396, 582, "18,00"),
    (440, 582, "tim"),
    (500, 582, "51,03"),
    (554, 582, "918,54"),
    (60, 566, "OB Vardag kväll"),
    (240, 566, "2026-06-01"),
    (320, 566, "2026-06-01"),
    (396, 566, "13,00"),
    (440, 566, "tim"),
    (500, 566, "25,52"),
    (554, 566, "331,76"),
    (60, 550, "Sjuklön dag -14, månadslön"),
    (240, 550, "2026-06-08"),
    (320, 550, "2026-06-09"),
    (396, 550, "16,00"),
    (440, 550, "tim"),
    (497, 550, "170,77"),
    (550, 550, "2 732,32"),
    (60, 534, "Sjukavdrag 100%, månadslön"),
    (240, 534, "2026-06-08"),
    (320, 534, "2026-06-09"),
    (396, 534, "16,00"),
    (440, 534, "tim"),
    (492, 534, "-213,46"),
    (545, 534, "-3 415,36"),
    (60, 518, "Karensavdrag"),
    (240, 518, "2026-06-08"),
    (320, 518, "2026-06-09"),
    (440, 518, "st"),
    (545, 518, "-1 366,15"),
    # Footer.
    (60, 470, "Skattetabell"),
    (170, 470, "33:1"),
    (210, 470, "Flexsaldo"),
    (300, 470, "0,00"),
    (390, 470, "Bruttolön"),
    (545, 470, "42 583,99"),
    (60, 456, "Jämkning"),
    (170, 456, "0,00"),
    (210, 456, "Kompsaldo"),
    (300, 456, "0,00"),
    (390, 456, "Preliminärskatt"),
    (548, 456, "-9 250,00"),
    (60, 428, "Tabellskattegrund"),
    (170, 428, "42 583,99"),
    (390, 428, "ATT UTBETALA"),
    (60, 414, "Ack. bruttolön"),
    (170, 414, "331 950,66"),
    (545, 414, "33 333,99"),
    (60, 400, "Ack. preliminärskatt"),
    (170, 400, "-77 338,00"),
]


def _escape(text: str) -> bytes:
    out = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return out.encode("latin-1", "replace")


def build_pdf() -> bytes:
    stream = bytearray()
    for x, y, text in LINES:
        stream += b"BT /F1 9 Tf 1 0 0 1 %d %d Tm (" % (x, y) + _escape(text) + b") Tj ET\n"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 700 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + bytes(stream) + b"\nendstream",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % number + body + b"\nendobj\n"

    xref_at = len(pdf)
    pdf += b"xref\n0 %d\n" % (len(objects) + 1)
    pdf += b"0000000000 65535 f \n"
    for offset in offsets:
        pdf += b"%010d 00000 n \n" % offset
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref_at)
    return bytes(pdf)


if __name__ == "__main__":
    target = Path(__file__).with_name("payslip_202606.pdf")
    target.write_bytes(build_pdf())
    print(f"Wrote {target} ({target.stat().st_size} bytes)")
