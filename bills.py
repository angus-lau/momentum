#!/usr/bin/env python3
"""
Create Xoro expense bills from bill specs and file the invoice PDFs.

Usage:
    python3 bills.py <spec.json> [<spec.json> ...]            # dry-run
    python3 bills.py <spec.json> [<spec.json> ...] --create   # post, verify GL, file

Each file holds one spec or a list of them (format: ``xoro_bills.py``). In practice
Claude reads the invoice PDF and writes the spec; this script does the rest
deterministically:

  1. finds the vendor (exact name — vendors are never created) and its bill history
  2. fills account / tax code / terms / store / AP from that history when omitted
  3. refuses unless the bill reproduces the invoice's subtotal, taxes and total
  4. skips an invoice number already on a bill for that vendor
  5. --create: posts, re-reads the GL (balanced, AP = total, expense = net + PST),
     and copies the PDF to Vendor Invoices - Trade/<vendor>/<YE fy>/<yy mm> INV#<n> <total>.pdf
"""

import os
import sys
import json
import shutil
from decimal import Decimal

import xoro_bills as xb


def load_specs(paths):
    specs = []
    for p in paths:
        with open(p) as f:
            data = json.load(f)
        specs.extend(data if isinstance(data, list) else [data])
    return specs


def file_pdf(src, dest):
    if os.path.exists(dest):
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)
    return True


def run(spec, x, create=False):
    pdf = os.path.expanduser(spec["pdf"])
    if not os.path.exists(pdf):
        raise xb.BillError("PDF not found: %s" % pdf)
    vendor = x.vendor(spec["vendor"])
    history = x.vendor_bills(vendor)
    defaults = xb.vendor_defaults(history)
    currency = spec.get("currency") or vendor["CurrencyCode"]
    date = xb.parse_date(spec["date"])
    if "exchange_rate" in spec:
        rate, rate_day = spec["exchange_rate"], None
    else:
        rate, rate_day = x.exchange_rate(date, currency)
    bill, s = xb.build_bill(spec, vendor, defaults, x.reference(), exchange_rate=rate)
    dest = xb.filed_path(spec, vendor["Name"])
    h = bill["billHeader"]

    print("\n%s  —  %s (vendor %s), invoice %s" % (os.path.basename(pdf), vendor["Name"],
                                                  vendor["Id"], spec["invoice_number"]))
    print("    %s, due %s (%s), %s%s, %d past bills" % (
        h["BillDate"], h["DueDate"], s["terms"], s["currency"],
        "" if s["currency"] == xb.HOME_CURRENCY else
        " @ %s%s" % (rate, " (rate from %s)" % rate_day if rate_day and rate_day != date else ""),
        len(history)))
    for name, amount, code, tax, non_cl in s["lines"]:
        print("    %-48s %10s  %-15s tax %.4f  non-claimable %.4f"
              % (name, amount, code or "no tax", tax, non_cl))
    print("    %-48s %10.2f  == invoice total" % ("TOTAL", h["TotalAmt"]))

    dup = [b["billHeader"] for b in history
           if (b["billHeader"].get("VendorBillNumber") or "").strip().lower()
           == spec["invoice_number"].strip().lower()]
    if dup:
        print("    already posted as %s" % dup[0]["BillNumber"])
    elif create:
        number = x.create(bill)
        print("    created %s" % number)
        expected = Decimal(spec["subtotal"]).quantize(Decimal("0.0001")) + s["non_claimable"]
        problems = xb.gl_problems(x.gl_rows(number, date), Decimal(str(spec["total"])), expected)
        if problems:
            print("    ⚠️  GL CHECK FAILED for %s: %s — fix the bill in Xoro" % (number, "; ".join(problems)))
        else:
            print("    GL verified: balanced, AP = total, expense = net + non-claimable tax")
    if create:
        print("    %s %s" % ("filed to" if file_pdf(pdf, dest) else "already filed at", dest))
    else:
        print("    would file to %s" % dest)


def main(argv):
    create = "--create" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 1
    x = xb.Xoro()
    failed = 0
    for spec in load_specs(paths):
        try:
            run(spec, x, create)
        except xb.BillError as e:
            failed += 1
            print("\n%s: REFUSED — %s" % (spec.get("invoice_number", "?"), e))
    if not create:
        print("\nDry run — pass --create to post and file.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
