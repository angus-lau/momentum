#!/usr/bin/env python3
"""
Build the month's PayPal reconciliation workbook, mirroring the hand-built ones.

Usage:
    python3 paypal_workbook.py 2026-08            # write into the month's FY folder
    python3 paypal_workbook.py 2026-08 --out DIR

Layout copied from `Paypal Reconciliation - 2026 07.xlsx`:

* **All** — every transaction, oldest first, no fills.
* **One sheet per currency** (alphabetical after All) — only the sale rows
  (Express Checkout Payment / Payment Refund) plus USD's User Initiated
  Withdrawals. The General Currency Conversion rows are **not** carried onto the
  currency sheets, which is why the check block's conversions figure is a SUMIFS
  against the All tab. Sorted by Description, then Date, oldest first.
* **Row fills** — yellow where the transaction made it into a Xoro bank deposit
  (or, for a withdrawal, a Fund Transfer); red where it did not, matching the
  `MISSING:` note on the deposit memo.
* **Check block on the USD sheet**, a few rows below the data. Unlike the
  hand-built version the exchange rate lives in its own labelled cell and the
  formulas reference it, instead of the number being typed into two formulas.

Both halves of the block are written, but the rate-dependent cells are only
filled once the deposits exist — Xoro assigns the rate when a deposit is
created, so the CAD column cannot be computed beforehand.

Columns follow the manual prep: Time Zone, Bank Name, Bank Account, Shipping and
Handling Amount and Sales Tax are dropped.
"""

import os
import sys
import csv
import datetime
from decimal import Decimal

import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

import paypal_statements as pps
import paypal_deposits as ppd

COLUMNS = ["Date", "Time", "Description", "Currency", "Gross ", "Fee ", "Net", "Balance",
           "Transaction ID", "From Email Address", "Name", "Invoice ID", "Reference Txn ID"]
MONEY_COLUMNS = {"Gross ", "Fee ", "Net", "Balance"}
COLUMN_WIDTHS = {"A": 12.0, "C": 13.0, "D": 12.0, "I": 16.0, "J": 20.0, "K": 12.0, "M": 18.0}

YELLOW = PatternFill("solid", fgColor="FFFFFF00")
RED = PatternFill("solid", fgColor="FFFF0000")

SALE_DESCRIPTIONS = ppd.SALE_DESCRIPTIONS
CONVERSION = ppd.CONVERSION
WITHDRAWAL = ppd.WITHDRAWAL
CURRENCY_SHEET_DESCRIPTIONS = SALE_DESCRIPTIONS | {WITHDRAWAL}


def read_rows(month):
    """The month's CSV as dicts, dropping the columns the manual prep drops."""
    folder = pps.month_folder(month)
    files = sorted(f for f in os.listdir(folder) if f.upper().endswith(".CSV"))
    if not files:
        raise SystemExit("no CSV in %s — run paypal_statements.py %s first" % (folder, month))
    path = os.path.join(folder, files[-1])
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d, m, y = r["Date"].split("/")
            out.append({
                "Date": datetime.date(int(y), int(m), int(d)),
                "Time": datetime.datetime.strptime(r["Time"], "%H:%M:%S").time(),
                "Description": r["Description"].strip(),
                "Currency": r["Currency"].strip(),
                "Gross ": float(r["Gross "].replace(",", "") or 0),
                "Fee ": float(r["Fee "].replace(",", "") or 0),
                "Net": float(r["Net"].replace(",", "") or 0),
                "Balance": float(r["Balance"].replace(",", "") or 0),
                "Transaction ID": r["Transaction ID"],
                "From Email Address": r["From Email Address"],
                "Name": r["Name"],
                "Invoice ID": r["Invoice ID"],
                "Reference Txn ID": r["Reference Txn ID"],
            })
    return out, path


def sort_key_all(row):
    return (row["Date"], row["Time"])


def sort_key_currency(row):
    """Description, then date — the order the manual sheets use."""
    return (row["Description"], row["Date"], row["Time"])


def _write_sheet(ws, rows, fills=None):
    for i, name in enumerate(COLUMNS, start=1):
        c = ws.cell(row=1, column=i, value=name)
        c.font = Font(bold=True)
    for r, row in enumerate(rows, start=2):
        for i, name in enumerate(COLUMNS, start=1):
            c = ws.cell(row=r, column=i, value=row[name])
            if name == "Date":
                c.number_format = "dd/mm/yyyy"
            elif name == "Time":
                c.number_format = "hh:mm:ss"
            elif name in MONEY_COLUMNS:
                c.number_format = "#,##0.00"
        fill = (fills or {}).get(row["Transaction ID"])
        if fill:
            for i in range(1, len(COLUMNS) + 1):
                ws.cell(row=r, column=i).fill = fill
    for col, width in COLUMN_WIDTHS.items():
        ws.column_dimensions[col].width = width


def check_block(ws, last_data_row, deposits=None, rate=None):
    """The USD sheet's cross-check block, a few rows below the data.

    The exchange rate gets its own cell (D80) and the formulas reference it, so
    the month's rate is visible and changed in one place rather than being typed
    into each formula.
    """
    start = last_data_row + 3
    ws.cell(row=start, column=3, value="Exchange rate (CAD/USD)").font = Font(bold=True)
    if rate is not None:
        ws.cell(row=start, column=4, value=float(rate))
    rate_ref = "$D$%d" % start

    head = start + 1
    ws.cell(row=head, column=4, value="CAD").font = Font(bold=True)
    ws.cell(row=head, column=5, value="USD").font = Font(bold=True)

    r = head + 1
    ws.cell(row=r, column=3, value="USD Equivalent Conversions")
    ws.cell(row=r, column=4, value="=E%d*%s" % (r, rate_ref))
    ws.cell(row=r, column=5,
            value='=SUMIFS(All!E:E, All!D:D, "USD", All!C:C, "General Currency Conversion")')
    conversions_row = r

    r += 1
    ws.cell(row=r, column=3, value="CAD amounts of PP received - SC")
    if deposits and deposits.get("service_centre") is not None:
        ws.cell(row=r, column=4, value=float(deposits["service_centre"]))
    ws.cell(row=r, column=5, value="=D%d/%s" % (r, rate_ref))

    r += 1
    ws.cell(row=r, column=3, value="Non-USD amounts received as USD in Xoro")
    if deposits and deposits.get("combined") is not None:
        ws.cell(row=r, column=5, value=float(deposits["combined"]))
        ws.cell(row=r, column=4, value="=E%d*%s" % (r, rate_ref))

    r += 1
    ws.cell(row=r, column=3, value="Difference = amount to be posted cc processing fees")
    ws.cell(row=r, column=5, value="=E%d-SUM(E%d:E%d)" % (conversions_row, conversions_row + 1, r - 1))

    r += 2
    first = r
    ws.cell(row=r, column=3, value="USD amounts")
    ws.cell(row=r, column=4,
            value='=SUMIF(C2:C%d,"Express Checkout Payment",E2:E%d)' % (last_data_row, last_data_row))
    r += 1
    ws.cell(row=r, column=3, value="FEES")
    ws.cell(row=r, column=4, value="=SUM(F2:F%d)" % last_data_row)
    r += 1
    ws.cell(row=r, column=3, value="REFUNDS")
    ws.cell(row=r, column=4,
            value='=SUMIF(C2:C%d,"Payment Refund",E2:E%d)' % (last_data_row, last_data_row))
    r += 1
    c = ws.cell(row=r, column=3, value="Deposits in USD, MINUS refunds, MINUS fees")
    c.font = Font(bold=True)
    total = ws.cell(row=r, column=4, value="=SUM(D%d:D%d)" % (first, r - 1))
    total.font = Font(bold=True)
    total.fill = YELLOW           # the figure the 1143 statement line uses
    total.number_format = "#,##0.00"
    for rr in range(conversions_row, r + 1):
        for col in (4, 5):
            cell = ws.cell(row=rr, column=col)
            if cell.value is not None:
                cell.number_format = "#,##0.00"


def build(month, out_dir=None):
    rows, source = read_rows(month)
    print("read %d row(s) from %s" % (len(rows), os.path.basename(source)))

    deposited, missing_ids, deposit_totals, rate = ppd.deposit_status(month, rows)
    fills = {}
    for row in rows:
        if row["Description"] not in CURRENCY_SHEET_DESCRIPTIONS:
            continue
        tid = row["Transaction ID"]
        fills[tid] = RED if tid in missing_ids else (YELLOW if tid in deposited else None)
    fills = {k: v for k, v in fills.items() if v}

    wb = openpyxl.Workbook()
    ws_all = wb.active
    ws_all.title = "All"
    _write_sheet(ws_all, sorted(rows, key=sort_key_all))

    currencies = sorted({r["Currency"] for r in rows if r["Description"] in CURRENCY_SHEET_DESCRIPTIONS})
    for cur in currencies:
        sheet_rows = sorted((r for r in rows
                             if r["Currency"] == cur
                             and r["Description"] in CURRENCY_SHEET_DESCRIPTIONS),
                            key=sort_key_currency)
        ws = wb.create_sheet(cur)
        _write_sheet(ws, sheet_rows, fills)
        if cur == "USD":
            check_block(ws, len(sheet_rows) + 1, deposit_totals, rate)

    folder = out_dir or pps.month_folder(month)
    os.makedirs(folder, exist_ok=True)
    year, mon = month.split("-")
    name = "Paypal Reconciliation - %s %s" % (year, mon)
    xlsx = os.path.join(folder, name + ".xlsx")
    wb.save(xlsx)
    print("wrote %s" % xlsx)

    csv_path = os.path.join(folder, name + ".csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for row in sorted(rows, key=sort_key_all):
            w.writerow(row)
    print("wrote %s" % csv_path)

    yellow = sum(1 for v in fills.values() if v is YELLOW)
    red = sum(1 for v in fills.values() if v is RED)
    print("sheets: All + %s | %d row(s) yellow (deposited), %d red (missing)"
          % (", ".join(currencies), yellow, red))
    return xlsx


if __name__ == "__main__":
    argv = sys.argv[1:]
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)
    build(args[0], out_dir=out)
