#!/usr/bin/env python3
"""
Turn a Dayforce "Funds Summary" payroll PDF into a Xoro Vendor Bill for Ceridian.

Usage:
    python3 ceridian_bills.py <pdf> [<pdf> ...]            # dry-run
    python3 ceridian_bills.py <pdf> [<pdf> ...] --create   # post

Books wages/CPP/EI by department plus the Dayforce service fee, netted against the
LTD/STD benefits so the bill lands exactly on the PDF's "TOTAL PAYMENT DUE".

Bill shape (rules established over several pay periods — see ``AUTOMATIONS.md``):

  7660 Professional Fees   the service fee **before** GST, Tax = G (GST Only);
                           Xoro computes the GST from the line, so it is not a
                           line of its own
  7840 Employee Benefits   **LTD* and STD* only, summed, negative.** *LIFE and
                           *AD&D sit in the same 7840 block on the Journal Entry
                           but are not on the bill
  2900 Shareholder Loan    SP.DEDNS, negative — only when the period has one
  per department           7120/7380/7780 salaries, 7880 CPP, 7900 EI

Deliberately **not** on the bill, though present on the Journal Entry: the
unlabelled per-department "EMPLOYEE BENEFITS" sub-line, ACCRUED VAC (2270),
AD&D, *LIFE, and the 1160 net-pay/remittance credits (Ceridian sweeps those
directly; this bill is the expense-recognition and service-fee side).

The self-check is the whole point: if the lines don't total the PDF's TOTAL
PAYMENT DUE, something was included or excluded wrongly, and the bill is
refused rather than posted.
"""

import os
import re
import sys
import json
import datetime
from decimal import Decimal, ROUND_HALF_UP

VENDOR_ID, VENDOR_NAME = "317", "Ceridian Corporation"
AP_ACCOUNT_ID = "B7B8887984D745EE867FD5B84456"      # 2100 - Accounts Payable - Trade (CAD)
PAYMENT_TERM_ID = "1285"                            # Due on receipt -> due date = bill date
STORE_ID, STORE_NAME = "10001", "CA"
GST_TAX_CODE = "2"                                  # "G" GST Only
GST_ITEM = {"itemId": 110, "itemName": "GST Purchase 5%", "itemRatePerc": 5}
GST_RATE = Decimal("0.05")

ACCOUNTS = {
    "7120": {"Id": "B7D04105A81C4E8308BC2C204D98", "Name": "7120 - Salaries - Direct"},
    "7380": {"Id": "B7D04105A81CF513641280184965", "Name": "7380 - Salaries - Selling"},
    "7780": {"Id": "B7D04105A81D4CA5DB31996F497F", "Name": "7780 - Salaries - Administration"},
    "7880": {"Id": "B7D04105A81D3B1BC6DDDE474838", "Name": "7880 - CPP Employer's Expense"},
    "7900": {"Id": "B7D04105A81DDCF7E7FE14904529", "Name": "7900 - EI Employer's Expense"},
    "7660": {"Id": "B7D04105A81DDE822DF3BCBE4036", "Name": "7660 - Professional Fees"},
    "7840": {"Id": "B7D04105A81DE41D478BBD874B0A", "Name": "7840 - Employee Benefits"},
    "2900": {"Id": "B7D04105A81BDEB39299438848B6", "Name": "2900 - Shareholder Loan - Simon Pennell"},
}
SALARY_ACCOUNTS = {"7120", "7380", "7780"}
DEPT_ACCOUNTS = SALARY_ACCOUNTS | {"7880", "7900"}


class BalanceError(Exception):
    """The assembled lines don't total the PDF's TOTAL PAYMENT DUE."""


def money(s):
    return Decimal(str(s).replace(",", "").replace("$", "").strip())


def gst_on(amount):
    return (Decimal(amount) * GST_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def period_date(path):
    """The pay period from the filename, e.g. 20260815.pdf -> 2026-08-15."""
    m = re.search(r"(\d{4})(\d{2})(\d{2})", os.path.basename(path))
    if not m:
        raise SystemExit("cannot read a period date from %r" % os.path.basename(path))
    return datetime.date(*(int(g) for g in m.groups()))


# ---------- parsing ----------

def parse_funds_summary(text):
    """Invoice number, total due, and the service fee split from the GST."""
    inv = re.search(r"Invoice Number\s+(\S+)", text)
    total = re.search(r"TOTAL PAYMENT DUE[\s.]*\$?([\d,]+\.\d{2})", text)
    gst = re.search(r"G\.\s*S\.\s*T\.\s+([\d,]+\.\d{2})", text)
    fee_total = re.search(r"^TOTAL\s+([\d,]+\.\d{2})", text, re.M)
    if not (inv and total and gst and fee_total):
        raise SystemExit("Funds Summary missing invoice/total/GST/fee")
    return {
        "invoice_number": inv.group(1),
        "total_due": money(total.group(1)),
        "gst": money(gst.group(1)),
        # the labelled TOTAL includes GST; the bill line carries the amount before it
        "service_fee": money(fee_total.group(1)) - money(gst.group(1)),
    }


def parse_journal_entry(text):
    """Per-department amounts, the LTD/STD benefits, and any SP.DEDNS.

    The page lists a department once and then continues with further accounts on
    their own lines, so the current department is carried forward. Only the
    accounts that belong on the bill are kept — everything else on the page
    (ACCRUED VAC, the 1160 credits, *LIFE, *AD&D, the unlabelled per-department
    EMPLOYEE BENEFITS sub-line) is ignored by simply not matching.
    """
    departments, benefits, sp = {}, Decimal(0), Decimal(0)
    dept = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # benefits and shareholder deductions are tagged by description, not department
        m = re.match(r"^\d+\s+7840\s+([\d,]+\.\d{2})\s+(LTD\*|STD\*)", line)
        if m:
            benefits += money(m.group(1))
            continue
        m = re.match(r"^\d+\s+2900\s+([\d,]+\.\d{2})\s+SP\.DEDNS", line)
        if m:
            sp += money(m.group(1))
            continue
        # "100 7120 7,866.68 SAL. AND WAGES" starts a department
        m = re.match(r"^(\d{3})\s+(\d{4})\s+([\d,]+\.\d{2})\s+(.+)$", line)
        if m and m.group(2) in DEPT_ACCOUNTS:
            dept = m.group(1)
            departments.setdefault(dept, {})[m.group(2)] = money(m.group(3))
            continue
        # "7880 442.88 CPP/QPP" continues the current department
        m = re.match(r"^(\d{4})\s+([\d,]+\.\d{2})\s+(.+)$", line)
        if m and dept and m.group(1) in DEPT_ACCOUNTS:
            departments[dept][m.group(1)] = money(m.group(2))
    return {"departments": departments, "benefits": benefits, "sp_dedns": sp}


def read_pdf(path):
    """(funds summary, journal entry) parsed from the PDF.

    Pages are found by content: the Funds Summary is page 1 in most periods but
    page 2 when Dayforce prepends a holiday notice.
    """
    import pdfplumber
    import contextlib
    import io
    with contextlib.redirect_stderr(io.StringIO()):
        with pdfplumber.open(path) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
    fs = next((t for t in pages if "TOTAL PAYMENT DUE" in t), None)
    je = next((t for t in pages if t.startswith("Journal Entry")), None)
    if fs is None or je is None:
        raise SystemExit("%s: could not find the Funds Summary / Journal Entry pages"
                         % os.path.basename(path))
    return parse_funds_summary(fs), parse_journal_entry(je)


# ---------- the bill ----------

def _line(seq, account, amount, tax=None):
    line = {"AccountId": account["Id"], "AccountName": account["Name"],
            "Amount": "%.2f" % amount, "TaxCodeId": "", "Memo": "", "ProjectClassId": "",
            "EntityTypeId": "", "EntityId": "", "EntityName": "", "EntityCurrencyId": 0,
            "IntAccntId": "", "StoreId": STORE_ID, "StoreName": STORE_NAME, "LineSeq": seq}
    if tax is not None:
        line["TaxCodeId"] = GST_TAX_CODE
        line["TaxData"] = {"totalAmount": float(tax),
                           "taxItems": [dict(GST_ITEM, itemAmount=float(tax),
                                             lineAmount="%.2f" % amount)]}
        line["TaxAmt"] = float(tax)
    return line


def build_bill(funds, journal, date):
    """The ``billJsonObj`` for BillWebMethods.createNewBill."""
    fee = funds["service_fee"]
    gst = gst_on(fee)
    lines, total = [], Decimal(0)

    lines.append(_line(len(lines) + 1, ACCOUNTS["7660"], fee, tax=gst))
    total += fee
    if journal["benefits"]:
        lines.append(_line(len(lines) + 1, ACCOUNTS["7840"], -journal["benefits"]))
        total -= journal["benefits"]
    if journal["sp_dedns"]:
        lines.append(_line(len(lines) + 1, ACCOUNTS["2900"], -journal["sp_dedns"]))
        total -= journal["sp_dedns"]
    for dept in sorted(journal["departments"]):
        for code in sorted(journal["departments"][dept]):
            amount = journal["departments"][dept][code]
            lines.append(_line(len(lines) + 1, ACCOUNTS[code], amount))
            total += amount

    grand = (total + gst).quantize(Decimal("0.01"))
    if grand != funds["total_due"]:
        raise BalanceError(
            "lines total %s but the PDF says TOTAL PAYMENT DUE %s (out by %s) — "
            "something was included or excluded wrongly"
            % (grand, funds["total_due"], grand - funds["total_due"]))

    d = date.strftime("%m/%d/%Y")
    header = {
        "Id": 0, "BillNumber": None, "TxnId": 0, "TxnNumber": 0, "StoreId": STORE_ID,
        "VendorId": VENDOR_ID, "TypeId": 20, "AccountPayableId": AP_ACCOUNT_ID,
        "TxnDate": d, "OldTxnDate": d, "BillDate": d, "DueDate": d, "DiscountDate": "",
        "ExchangeRate": 1, "CurrencyId": 1, "PaymentTermId": PAYMENT_TERM_ID,
        "PaymentTermName": None, "RefNo": "", "TotalAmt": float(grand),
        "TotalTaxAmt": float(gst), "OldTotalAmt": 0, "ReconcileFlag": False, "Memo": "",
        "BillFromCompanyName": VENDOR_NAME, "BillFromFirstName": "", "BillFromLastName": "",
        "BillFromAddress2": "", "BillFromPhoneNumber": "", "BillFromEmail": "",
        "StoreName": STORE_NAME, "VendorBillNumber": funds["invoice_number"],
        "ProjectClassId": "", "BuyerId": "", "TotalCBM": 0,
        "UndoReconcilation": False, "IsConvertToBill": False, "RestrictVoidExpenseBill": False,
        "IsMultiReconcilation": False, "UndoReconcileLinkedExpenseBills": False,
        "TaxServiceHashCode": 0, "EntityUseCode": None,
    }
    return {"billHeader": header, "billItemLineArr": [], "billExpenseLineArr": lines}


def existing_bill(vendor_bill_number):
    """The Ceridian bill already carrying this invoice number, if any."""
    from xoro_api import XoroClient
    for b in XoroClient.from_config().get_bills(vendor_name=VENDOR_NAME):
        if b["billHeader"].get("VendorBillNumber") == vendor_bill_number:
            return b["billHeader"]
    return None


def main(paths, create=False):
    from xoro_webmethods import WebMethodClient
    client = WebMethodClient.from_config() if create else None
    for path in paths:
        date = period_date(path)
        funds, journal = read_pdf(path)
        bill = build_bill(funds, journal, date)
        h = bill["billHeader"]
        print("\n%s  —  %s, invoice %s" % (os.path.basename(path), h["TxnDate"], h["VendorBillNumber"]))
        for l in bill["billExpenseLineArr"]:
            tax = "  (Tax G, GST %.2f)" % l["TaxAmt"] if l.get("TaxAmt") else ""
            print("    %-42s %10s%s" % (l["AccountName"], l["Amount"], tax))
        print("    %-42s %10.2f   == TOTAL PAYMENT DUE %s"
              % ("TOTAL", h["TotalAmt"], funds["total_due"]))
        dup = existing_bill(h["VendorBillNumber"])
        if dup:
            print("    already posted as %s — skipping" % dup.get("BillNumber"))
            continue
        if not create:
            continue
        r = client.call("BillWebMethods", "createNewBill", billJsonObj=json.dumps(bill))
        if not r.get("Result"):
            raise SystemExit("createNewBill failed: %s" % r.get("Message"))
        print("    %s" % r.get("Message"))
    if not create:
        print("\nDry run — pass --create to post.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    main(args, create="--create" in sys.argv)
