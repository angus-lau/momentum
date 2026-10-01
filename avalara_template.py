#!/usr/bin/env python3
"""
Fill the Avalara submission template from the Zonos landed cost data.

Reproduces how the monthly `AvaTemplate.xlsx` has been built by hand (see
`Vendor Invoices - Trade/Avalara Europe Ltd (UK EU VAT)/FY 2026/26 02/`), reading
the orders straight from the Zonos API via ``zonos_landed_cost``.

Row selection and arithmetic, both taken from the February 2026 file:

  * one row per Zonos order whose destination is an **EU** member state — filed
    under the FR IOSS registration — or **GB**, filed under the HMRC registration.
    Everywhere else (AU, JP, MX, IL, KR, TW, SG, ...) is out of scope and dropped.
    Both ``CUSTOMS BILL`` and ``TAX REMITTANCE`` rows are included: February
    carried 16 of the former and 2 of the latter.
  * ``Taxable Basis`` = the export's ``orderTotal``
  * ``Value VAT``     = Taxable Basis x the destination's standard VAT rate, to 2dp
  * ``Total``         = Taxable Basis + Value VAT
  * ``VAT rate``      = Value VAT / Taxable Basis — the quotient, not the statutory
    rate, which is why the hand-built file shows 0.26999590... for Hungary's 27%.

``--validate`` rebuilds February and diffs it against the real file.

NOTE on the rate table: VAT rates change, and this table is what reproduces the
February file exactly. Check a new period's rates before filing — a rate that moved
mid-period will not be caught by the February validation.

Usage:
    python3 avalara_template.py 2026-06-01 2026-09-30
    python3 avalara_template.py 2026-06-01 2026-09-30 --out DIR
    python3 avalara_template.py --validate
"""

import os
import re
import string
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

import openpyxl
from openpyxl.styles import Font

import zonos_landed_cost as zlc

SELLER_NAME = "St. Moritz Watch Corp."
CUSTOMER_VAT = "PRIVATE INDIVIDUAL"
DESCRIPTION = "Goods"
CURRENCY = "USD"          # the hand-built file carries USD on every row
DISPATCH = "CA"

HEADERS = ["Invoice Number", "Transaction Date", "Invoice Date", "Currency",
           "Transaction type", "Country Dispatch", "Country Arrival", "Seller Name",
           "Seller VAT Number", "Customer VAT Number", "Customer Name", "Description",
           "Taxable Basis", "Value VAT", "Total", "VAT rate", "GTU Code", "Document Code"]

# Standard VAT rates, as percentages. The eight marked (*) are pinned by the
# February file; the rest are the published standard rates.
VAT_RATE = {
    "AT": 20, "BE": 21, "BG": 20, "HR": 25, "CY": 19, "CZ": 21, "DK": 25, "EE": 24,
    "FI": Decimal("25.5"), "FR": 20, "DE": 19, "GR": 24, "HU": 27, "IE": 23, "IT": 22,
    "LV": 21, "LT": 21, "LU": 17, "MT": 18, "NL": 21, "PL": 23, "PT": 23, "RO": 21,
    "SK": 23, "SI": 22, "ES": 21, "SE": 25,
}
EU = set(VAT_RATE)
GB_RATE = 20

VALIDATE_TEMPLATE = os.path.join(zlc.AVALARA_DIR, "FY 2026", "26 02", "26 02  AvaTemplate.xlsx")


class ScopeError(RuntimeError):
    pass


def registrations():
    """{type: taxIdNumber} from the Zonos account, so the numbers aren't hardcoded."""
    out = {}
    for r in zlc.tax_registrations():
        out[r["type"]] = r["taxIdNumber"]
    return out


def _q2(d):
    return Decimal(d).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def template_rows(csv_rows, regs):
    """The in-scope rows, as dicts keyed by HEADERS."""
    ioss, hmrc = regs.get("IOSS"), regs.get("HMRC")
    out, skipped = [], {}
    for r in csv_rows:
        cc = (r.get("countryCode") or "").upper()
        if cc in EU:
            rate, txn_type, seller_vat = Decimal(str(VAT_RATE[cc])), "IOSS", ioss
        elif cc == "GB":
            rate, txn_type, seller_vat = Decimal(GB_RATE), "UK VAT", hmrc
        else:
            skipped[cc] = skipped.get(cc, 0) + 1
            continue
        if not seller_vat:
            raise ScopeError("no %s registration on the Zonos account for %s" % (txn_type, cc))
        basis = Decimal(r["orderTotal"] or "0")
        vat = _q2(basis * rate / 100)
        when = date.fromisoformat(r["orderDate"])
        out.append({
            "Invoice Number": r["orderReference"],
            "Transaction Date": when, "Invoice Date": when,
            "Currency": CURRENCY, "Transaction type": txn_type,
            "Country Dispatch": DISPATCH, "Country Arrival": cc,
            "Seller Name": SELLER_NAME, "Seller VAT Number": seller_vat,
            "Customer VAT Number": CUSTOMER_VAT, "Customer Name": r["customerName"],
            "Description": DESCRIPTION,
            "Taxable Basis": basis, "Value VAT": vat, "Total": basis + vat,
            # the quotient, matching the hand-built file's long decimals
            "VAT rate": (vat / basis) if basis else Decimal(0),
            "GTU Code": "", "Document Code": "",
        })
    out.sort(key=lambda x: (x["Transaction Date"], x["Invoice Number"]))
    return out, skipped


def write_xlsx(rows, path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for i, h in enumerate(HEADERS, start=1):
        ws.cell(row=1, column=i, value=h).font = Font(bold=True)
    for r, row in enumerate(rows, start=2):
        for i, h in enumerate(HEADERS, start=1):
            v = row[h]
            c = ws.cell(row=r, column=i, value=float(v) if isinstance(v, Decimal) else v)
            if h in ("Transaction Date", "Invoice Date"):
                c.number_format = "yyyy-mm-dd"
            elif h in ("Taxable Basis", "Value VAT", "Total"):
                c.number_format = "#,##0.00"
    for col, w in (("A", 14), ("B", 16), ("C", 14), ("E", 15), ("H", 22), ("I", 18),
                   ("J", 20), ("K", 24), ("M", 13), ("N", 11), ("O", 12), ("P", 20)):
        ws.column_dimensions[col].width = w
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)
    return path


def read_existing(path):
    """Read a hand-built template without openpyxl, whose stylesheet it rejects."""
    NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(path)
    shared = ["".join(t.text or "" for t in si.iter(NS + "t"))
              for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter(NS + "row"):
        cells = {}
        for c in row.iter(NS + "c"):
            col = re.match(r"([A-Z]+)", c.get("r")).group(1)
            v = c.find(NS + "v")
            cells[col] = (shared[int(v.text)] if c.get("t") == "s" and v is not None
                          else (v.text if v is not None else ""))
        rows.append(cells)
    letters = list(string.ascii_uppercase[:len(HEADERS)])
    out = []
    for cells in rows[1:]:
        if not (cells.get("A") or "").strip():
            continue
        out.append({h: (cells.get(l) or "") for h, l in zip(HEADERS, letters)})
    return out


def validate():
    regs = registrations()
    csv_rows, _ = zlc.build("2026-01-01", "2026-02-28")
    got, skipped = template_rows(csv_rows, regs)
    want = read_existing(VALIDATE_TEMPLATE)
    print("hand-built rows %d | generated %d  (out of scope: %s)"
          % (len(want), len(got), ", ".join("%s x%d" % kv for kv in sorted(skipped.items()))))
    by_inv = {r["Invoice Number"]: r for r in got}
    want_inv = {r["Invoice Number"] for r in want}
    extra = [i for i in by_inv if i not in want_inv]
    missing = [i for i in want_inv if i not in by_inv]
    if extra:
        print("  generated but not in the hand-built file: %s" % ", ".join(sorted(extra)))
    if missing:
        print("  in the hand-built file but not generated: %s" % ", ".join(sorted(missing)))
    diffs = 0
    for w in want:
        g = by_inv.get(w["Invoice Number"])
        if not g:
            continue
        for h in HEADERS:
            a, b = w[h], g[h]
            if h in ("Transaction Date", "Invoice Date"):
                a = date(1899, 12, 30).toordinal() + int(float(a)) if a else None
                a = date.fromordinal(a) if a else None
            elif h in ("Taxable Basis", "Value VAT", "Total", "VAT rate"):
                a = Decimal(a) if str(a).strip() else Decimal(0)
                b = Decimal(b)
                if abs(a - b) < Decimal("0.00001"):
                    continue
            if str(a) != str(b):
                diffs += 1
                if diffs <= 20:
                    print("  %-8s %-18s hand=%r gen=%r" % (w["Invoice Number"], h, a, b))
    print("%d cell difference(s)%s" % (diffs, "" if diffs else "  — exact match"))
    return diffs == 0 and not missing


def default_out(end):
    e = date.fromisoformat(end)
    return os.path.join(zlc.AVALARA_DIR, "FY %d" % zlc.fiscal_year(e),
                        "%02d %02d" % (e.year % 100, e.month))


def main(argv):
    if "--validate" in argv:
        return 0 if validate() else 1
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        return 1
    start, end = args
    regs = registrations()
    print("registrations: %s" % ", ".join("%s %s" % kv for kv in sorted(regs.items())))
    csv_rows, warnings = zlc.build(start, end)
    rows, skipped = template_rows(csv_rows, regs)
    print("%d Zonos order(s) %s..%s" % (len(csv_rows), start, end))
    print("  in scope (EU IOSS / GB): %d" % len(rows))
    if skipped:
        print("  out of scope: %s" % ", ".join("%s x%d" % kv for kv in sorted(skipped.items())))
    for w in warnings:
        print("  warning: %s" % w)
    e = date.fromisoformat(end)
    name = "%02d %02d  AvaTemplate.xlsx" % (e.year % 100, e.month)
    path = write_xlsx(rows, os.path.join(out or default_out(end), name))
    print("wrote %s" % path)
    if rows:
        tb = sum(r["Taxable Basis"] for r in rows)
        vt = sum(r["Value VAT"] for r in rows)
        print("  taxable basis %.2f | VAT %.2f" % (tb, vt))
    else:
        print("\nNOTE: no in-scope rows — Zonos has no EU or GB orders in this period.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
