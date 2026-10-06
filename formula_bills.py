#!/usr/bin/env python3
"""
Turn a Formula Networks invoice PDF into a Xoro Vendor Bill and file the PDF.

Usage:
    python3 formula_bills.py <pdf> [<pdf> ...]                     # dry-run
    python3 formula_bills.py <pdf> [<pdf> ...] --create            # post + file
    python3 formula_bills.py <pdf> --create --account 7620         # e.g. hardware

Vendor Formula Resource Group Ltd. (334), AP 2100 Trade (CAD), store CA, NET 30.
One expense line per tax letter on the invoice:

  GP  (GST 5% + PST 7%)  7520 Dues, Memberships and Subscriptions, Standard (BC)
                         — the Microsoft 365 / Symantec subscriptions
  G   (GST 5% only)      7660 Professional Fees, GST Only — hourly labour

Any other tax letter is refused. ``--account`` sends every line to another
expense account (7520 / 7620 / 7660 / 7700) when the default is wrong.

**PST is non-recoverable and must be sent as the line's ``TaxAmtNonCl``.** The
Xoro UI computes it client-side; the server does not, so without it the GL books
the expense net and the PST simply disappears (the bill total still looks right).

The bill is refused unless its lines reproduce the PDF's subtotal, GST, PST and
Total Amount. A bill already posted under the same invoice number is skipped.
With ``--create`` the PDF is copied (the original is left in place) to
``Vendor Invoices - Trade/Formula Resources Group/YE <fy>/<yy mm> INV#<n> <total>.pdf``.
"""

import os
import re
import sys
import json
import shutil
import datetime
from decimal import Decimal, ROUND_HALF_UP

VENDOR_ID, VENDOR_NAME = "334", "Formula Resource Group Ltd."
AP_ACCOUNT_ID = "B7B8887984D745EE867FD5B84456"      # 2100 - Accounts Payable - Trade (CAD)
PAYMENT_TERM_ID, NET_DAYS = "1286", 30              # NET 30
STORE_ID, STORE_NAME = "10001", "CA"
FILING_DIR = ("/Users/angus/Library/CloudStorage/OneDrive-St.MoritzWatch/Accounting Docs/"
              "Vendor Invoices - Trade/Formula Resources Group")

ACCOUNTS = {
    "7520": {"Id": "B7D04105A81C423AC009C59D4295", "Name": "7520 - Dues, Memberships and Subscriptions"},
    "7620": {"Id": "B7D04105A81DA9B29BFA90D644BE", "Name": "7620 - Office Supplies"},
    "7660": {"Id": "B7D04105A81DDE822DF3BCBE4036", "Name": "7660 - Professional Fees"},
    "7700": {"Id": "B7D04105A81D7CAA1696635E497A", "Name": "7700 - Repairs and Maintenance"},
}
GST_ITEM = {"itemId": 110, "itemName": "GST Purchase 5%", "itemRatePerc": 5, "rate": Decimal("0.05")}
PST_ITEM = {"itemId": 130, "itemName": "PST Purchase (BC) 7%", "itemRatePerc": 7, "rate": Decimal("0.07")}
# invoice tax letter -> (Xoro tax code, default account, tax items)
TAX_LETTERS = {
    "GP": ("3", "7520", [GST_ITEM, PST_ITEM]),     # Standard (BC)
    "G": ("2", "7660", [GST_ITEM]),                # GST Only
}

CENT = Decimal("0.01")


class BalanceError(Exception):
    """The bill can't reproduce the invoice's totals, or has a line it can't code."""


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


# ---------- the bill ----------

def _line(seq, account, amount, tax_code, items):
    taxes = [(item, amount * item["rate"]) for item in items]
    non_claimable = sum((t for item, t in taxes if item is PST_ITEM), Decimal(0))
    total_tax = sum((t for _, t in taxes), Decimal(0))
    return {
        "AccountId": account["Id"], "AccountName": account["Name"], "Amount": "%.2f" % amount,
        "TaxCodeId": tax_code, "Memo": "", "ProjectClassId": "", "EntityTypeId": "",
        "EntityId": "", "EntityName": "", "EntityCurrencyId": 0, "IntAccntId": "",
        "StoreId": STORE_ID, "StoreName": STORE_NAME, "LineSeq": seq,
        "TaxData": {"totalAmount": float(total_tax), "taxItems": [
            {"itemId": item["itemId"], "itemName": item["itemName"], "itemAmount": float(t),
             "lineAmount": "%.2f" % amount, "itemRatePerc": item["itemRatePerc"]}
            for item, t in taxes]},
        "TaxAmt": float(total_tax),
        "TaxAmtNonCl": float(non_claimable),
    }


def build_bill(inv, account=None):
    """The ``billJsonObj`` for BillWebMethods.createNewBill."""
    if account is not None and account not in ACCOUNTS:
        raise BalanceError("unknown account %s (expected one of %s)" % (account, ", ".join(ACCOUNTS)))
    by_letter = {}
    for l in inv["lines"]:
        if l["tax"] not in TAX_LETTERS:
            raise BalanceError("line %r has tax letter %r — only %s are coded"
                               % (l["description"], l["tax"], "/".join(TAX_LETTERS)))
        by_letter[l["tax"]] = by_letter.get(l["tax"], Decimal(0)) + l["amount"]

    lines = []
    for letter in TAX_LETTERS:
        if letter in by_letter:
            code, default, items = TAX_LETTERS[letter]
            lines.append(_line(len(lines) + 1, ACCOUNTS[account or default],
                               by_letter[letter], code, items))

    subtotal = sum(by_letter.values(), Decimal(0))
    gst = sum((Decimal(str(i["itemAmount"])) for l in lines for i in l["TaxData"]["taxItems"]
               if i["itemId"] == GST_ITEM["itemId"]), Decimal(0)).quantize(CENT, ROUND_HALF_UP)
    pst = sum((Decimal(str(l["TaxAmtNonCl"])) for l in lines), Decimal(0)).quantize(CENT, ROUND_HALF_UP)
    checks = [("subtotal", subtotal, inv["subtotal"]), ("GST", gst, inv["gst"]),
              ("PST", pst, inv["pst"]),
              ("Total Amount", inv["subtotal"] + inv["gst"] + inv["pst"], inv["total"])]
    for name, ours, pdf in checks:
        if ours != pdf:
            raise BalanceError("%s: lines give %s but the invoice says %s" % (name, ours, pdf))

    d = inv["date"].strftime("%m/%d/%Y")
    due = (inv["date"] + datetime.timedelta(days=NET_DAYS)).strftime("%m/%d/%Y")
    header = {
        "Id": 0, "BillNumber": None, "TxnId": 0, "TxnNumber": 0, "StoreId": STORE_ID,
        "VendorId": VENDOR_ID, "TypeId": 20, "AccountPayableId": AP_ACCOUNT_ID,
        "TxnDate": d, "OldTxnDate": d, "BillDate": d, "DueDate": due, "DiscountDate": "",
        "ExchangeRate": 1, "CurrencyId": 1, "PaymentTermId": PAYMENT_TERM_ID,
        "PaymentTermName": None, "RefNo": "", "TotalAmt": float(inv["total"]),
        "TotalTaxAmt": float(gst + pst), "OldTotalAmt": 0, "ReconcileFlag": False, "Memo": "",
        "BillFromCompanyName": VENDOR_NAME, "BillFromFirstName": "", "BillFromLastName": "",
        "BillFromAddress2": "", "BillFromPhoneNumber": "", "BillFromEmail": "",
        "StoreName": STORE_NAME, "VendorBillNumber": inv["number"],
        "ProjectClassId": "", "BuyerId": "", "TotalCBM": 0,
        "UndoReconcilation": False, "IsConvertToBill": False, "RestrictVoidExpenseBill": False,
        "IsMultiReconcilation": False, "UndoReconcileLinkedExpenseBills": False,
        "TaxServiceHashCode": 0, "EntityUseCode": None,
    }
    return {"billHeader": header, "billItemLineArr": [], "billExpenseLineArr": lines}


# ---------- filing ----------

def filed_path(inv):
    """FY ends July 31, so Aug–Dec file under the next year's YE folder."""
    d = inv["date"]
    fy = d.year + 1 if d.month >= 8 else d.year
    return os.path.join(FILING_DIR, "YE %d" % fy, "%s INV#%s %.2f.pdf"
                        % (d.strftime("%y %m"), inv["number"], inv["total"]))


def file_pdf(src, inv):
    dest = filed_path(inv)
    if os.path.exists(dest):
        return dest, False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)
    return dest, True


# ---------- Xoro ----------

def existing_bill(vendor_bill_number):
    """The Formula bill already carrying this invoice number, if any."""
    from xoro_api import XoroClient
    for b in XoroClient.from_config().get_bills(vendor_name=VENDOR_NAME):
        if b["billHeader"].get("VendorBillNumber") == vendor_bill_number:
            return b["billHeader"]
    return None


def main(paths, create=False, account=None):
    from xoro_webmethods import WebMethodClient
    client = WebMethodClient.from_config() if create else None
    for path in paths:
        inv = read_pdf(path)
        bill = build_bill(inv, account)
        h = bill["billHeader"]
        print("\n%s  —  invoice %s, %s (due %s)" % (os.path.basename(path), inv["number"],
                                                   h["BillDate"], h["DueDate"]))
        for l in inv["lines"]:
            print("    %-3s %10s  %s" % (l["tax"], l["amount"], l["description"]))
        for l in bill["billExpenseLineArr"]:
            print("  → %-45s %10s  tax %s  (GST+PST %.3f, non-claimable %.3f)"
                  % (l["AccountName"], l["Amount"], l["TaxCodeId"], l["TaxAmt"], l["TaxAmtNonCl"]))
        print("    %-45s %10.2f  == invoice Total Amount" % ("TOTAL", h["TotalAmt"]))
        dup = existing_bill(inv["number"])
        if dup:
            print("    already posted as %s" % dup.get("BillNumber"))
        elif create:
            r = client.call("BillWebMethods", "createNewBill", billJsonObj=json.dumps(bill))
            if not r.get("Result"):
                raise SystemExit("createNewBill failed: %s" % r.get("Message"))
            print("    %s" % r.get("Message"))
        if create:
            dest, copied = file_pdf(path, inv)
            print("    %s %s" % ("filed to" if copied else "already filed at", dest))
        else:
            print("    would file to %s" % filed_path(inv))
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
