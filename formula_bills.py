#!/usr/bin/env python3
"""
Turn Formula Networks invoice PDFs into Xoro Vendor Bills and file the PDFs.

Usage:
    python3 formula_bills.py <pdf> [<pdf> ...]                     # dry-run
    python3 formula_bills.py <pdf> [<pdf> ...] --create            # post + file
    python3 formula_bills.py <pdf> --create --account 7620         # e.g. hardware

The deterministic front end for one vendor: it parses the PDF into a bill spec and
hands it to the general engine (``xoro_bills.py`` / ``bills.py``), which does the
checks, posting, GL verification and filing. One expense line per tax letter:

  GP  (GST 5% + PST 7%)  7520 Dues, Memberships and Subscriptions, Standard (BC)
                         — the Microsoft 365 / Symantec subscriptions
  G   (GST 5% only)      7660 Professional Fees, GST Only — hourly labour

Any other tax letter is refused. ``--account`` sends every line to another
expense account when the default is wrong.
"""

import re
import sys
import datetime
from decimal import Decimal


VENDOR_NAME = "Formula Resource Group Ltd."
FOLDER = "Formula Resources Group"
ACCOUNTS = ("7520", "7620", "7660", "7700")
# invoice tax letter -> (default account, Xoro tax code)
TAX_LETTERS = {"GP": ("7520", "Standard (BC)"), "G": ("7660", "GST Only")}


class BalanceError(Exception):
    """The invoice has a line this script can't code."""


def money(s):
    return Decimal(str(s).replace(",", "").replace("$", "").strip())


# ---------- parsing ----------

def parse_invoice(text):
    def grab(pattern, required=True):
        m = re.search(pattern, text, re.M)
        if not m and required:
            raise SystemExit("invoice is missing %r" % pattern)
        return m.group(1) if m else None

    lines = []
    for m in re.finditer(r"^(-?[\d.]+)\s+(.+?)\s+([A-Z]{1,3})\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})$",
                         text, re.M):
        lines.append({"description": m.group(2), "tax": m.group(3), "amount": money(m.group(5))})
    pst = grab(r"^PST\s+([\d,]+\.\d{2})$", required=False)
    return {
        "number": grab(r"Invoice#:\s*(\S+)"),
        "date": datetime.datetime.strptime(grab(r"^Date:\s*(\d{1,2} \w{3}, \d{4})"), "%d %b, %Y").date(),
        "lines": lines,
        "subtotal": money(grab(r"Subtotal:\s*([\d,]+\.\d{2})")),
        "gst": money(grab(r"^GST\s+([\d,]+\.\d{2})$")),
        "pst": money(pst) if pst else Decimal(0),
        "total": money(grab(r"Total Amount\s+([\d,]+\.\d{2})")),
    }


def read_pdf(path):
    import pdfplumber
    import contextlib
    import io
    with contextlib.redirect_stderr(io.StringIO()):
        with pdfplumber.open(path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return parse_invoice(text)


def to_spec(inv, pdf, account=None):
    """The bill spec for ``bills.py``: one line per tax letter, in GP, G order."""
    if account is not None and account not in ACCOUNTS:
        raise BalanceError("unknown account %s (expected one of %s)" % (account, ", ".join(ACCOUNTS)))
    by_letter = {}
    for l in inv["lines"]:
        if l["tax"] not in TAX_LETTERS:
            raise BalanceError("line %r has tax letter %r — only %s are coded"
                               % (l["description"], l["tax"], "/".join(TAX_LETTERS)))
        by_letter.setdefault(l["tax"], []).append(l)
    lines = []
    for letter, (default, tax_code) in TAX_LETTERS.items():
        if letter in by_letter:
            lines.append({"description": "; ".join(l["description"] for l in by_letter[letter]),
                          "amount": float(sum((l["amount"] for l in by_letter[letter]), Decimal(0))),
                          "account": account or default, "tax_code": tax_code})
    taxes = {"GST": float(inv["gst"])}
    if inv["pst"]:
        taxes["PST"] = float(inv["pst"])
    return {"pdf": pdf, "vendor": VENDOR_NAME, "folder": FOLDER,
            "invoice_number": inv["number"], "date": inv["date"].isoformat(),
            "lines": lines, "taxes": taxes,
            "subtotal": float(inv["subtotal"]), "total": float(inv["total"])}


def main(paths, create=False, account=None):
    import bills
    import xoro_bills as xb
    x = xb.Xoro()
    for path in paths:
        bills.run(to_spec(read_pdf(path), path, account), x, create)
    if not create:
        print("\nDry run — pass --create to post and file.")


if __name__ == "__main__":
    argv = sys.argv[1:]
    account = None
    if "--account" in argv:
        i = argv.index("--account")
        account = argv[i + 1]
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    main(args, create="--create" in argv, account=account)
