#!/usr/bin/env python3
"""
Create the month's DHL GST / EU VAT / UK VAT bill in Xoro from the reconcile output.

Usage:
    python3 dhl_bill.py "/path/to/Amex Sofia 4002/26 08"            # dry-run: show the bill
    python3 dhl_bill.py "/path/to/Amex Sofia 4002/26 08" --create   # post it

Reads the TOTAL row of ``dhl_reconcile_lines.csv`` (written by ``dhl_reconcile.py``)
and books one Vendor Bill on **DHL Express Canada**, dated the Amex statement's
closing date (the ``YYYY-MM-DD.pdf`` in the folder):

    expense line  2251 - VAT NL - Paid   = EU VAT total   (no line tax)
    expense line  2245 - VAT UK - Paid   = UK VAT total   (no line tax)
    tax adjustment "GST Purchase 5%"     = GST total      (header TaxAdjItemArr)

so the bill total equals the "GST, EU, UK VAT" line on the 4002 statement. The
tax adjustment is what routes the GST to 2226 GST/HST Payable instead of an
expense account. Payload shape captured from the Xoro UI and proven live with
``CA-B002020`` (Aug 2026) — see ``../XORO_API.md`` → BillWebMethods.

Refuses to create a second bill if a DHL Express Canada bill for the same date and
total is already in the GL. Paying the bill from 2106 is still a manual step.
"""

import sys
import re
import csv
import json
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from xoro_webmethods import WebMethodClient  # noqa: E402
from xoro_api import XoroClient  # noqa: E402

# ---------- Xoro ids (momentum.xoro.one) ----------
VENDOR_ID = "387"                                   # DHL Express Canada
VENDOR_NAME = "DHL Express Canada"
STORE_ID, STORE_NAME = "10001", "CA"
AP_ACCOUNT_ID = "B7B8887984D745EE867FD5B84456"      # 2100 - Accounts Payable - Trade (CAD)
PAYMENT_TERM_ID = "1318"                            # "Credit Card", net 0 -> due = bill date
GST_TAX_ITEM = {"Id": 110, "Name": "GST Purchase 5%"}
LINE_ACCOUNTS = [  # (reconcile column, account id, account name)
    ("EU VAT", "B7D04105A81BE24FF29A8CEE4F4B", "2251 - VAT NL - Paid"),
    ("UK VAT", "B7D04105A81BCA1039CA3CB24CBE", "2245 - VAT UK - Paid"),
]
STATEMENT_PDF_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.pdf$")


class NothingToBill(Exception):
    """All three totals are zero."""


def read_totals(folder):
    """GST / EU VAT / UK VAT from the TOTAL row of dhl_reconcile_lines.csv."""
    path = folder / "dhl_reconcile_lines.csv"
    if not path.exists():
        raise SystemExit(f"No dhl_reconcile_lines.csv in {folder} — run dhl_reconcile.py first")
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("Invoice Number") == "TOTAL":
                return {k: round(float(row.get(k) or 0), 2) for k in ("GST", "EU VAT", "UK VAT")}
    raise SystemExit(f"No TOTAL row in {path}")


def statement_date(folder):
    """Closing date from the Amex statement PDF saved as YYYY-MM-DD.pdf, or None."""
    for p in folder.glob("*.pdf"):
        m = STATEMENT_PDF_RE.match(p.name)
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def vendor_bill_number(date):
    return f"DHL GST-VAT {date.isoformat()}"


def _expense_line(seq, acct_id, name, amount):
    return {"AccountId": acct_id, "AccountName": name, "Amount": f"{amount:.2f}", "TaxCodeId": "",
            "Memo": "", "ProjectClassId": "", "EntityTypeId": "", "EntityId": "", "EntityName": "",
            "EntityCurrencyId": 0, "IntAccntId": "", "StoreId": STORE_ID, "StoreName": STORE_NAME,
            "TaxData": {"totalAmount": 0, "taxItems": []}, "TaxAmt": 0, "TaxAmtNonCl": 0, "LineSeq": seq}


def build_bill(totals, date):
    """The ``billJsonObj`` for BillWebMethods.createNewBill (a dict; caller json-dumps it)."""
    gst = round(totals.get("GST", 0), 2)
    lines = [_expense_line(i + 1, acct_id, name, totals[col])
             for i, (col, acct_id, name) in enumerate(
                 (c, a, n) for c, a, n in LINE_ACCOUNTS if round(totals.get(c, 0), 2) > 0)]
    if not lines and gst <= 0:
        raise NothingToBill("GST, EU VAT and UK VAT are all zero — nothing to bill")
    tax_adj = [{**GST_TAX_ITEM, "Amount": gst, "Memo": ""}] if gst > 0 else []
    total = round(sum(float(l["Amount"]) for l in lines) + gst, 2)
    d = date.strftime("%m/%d/%Y")
    header = {
        "Id": 0, "BillNumber": None, "TxnId": 0, "TxnNumber": 0, "StoreId": STORE_ID, "VendorId": VENDOR_ID,
        "TypeId": 20, "AccountPayableId": AP_ACCOUNT_ID, "TxnDate": d, "OldTxnDate": d, "ExchangeRate": 1,
        "RefNo": "", "TotalAmt": total, "TotalTaxAmt": gst if gst > 0 else 0, "OldTotalAmt": 0,
        "ReconcileFlag": False,
        "Memo": f"DHL GST + EU/UK VAT — Amex 4002 statement {d}",
        "CurrencyId": 1, "PaymentTermId": PAYMENT_TERM_ID, "PaymentTermName": None, "DiscountDate": "",
        "DueDate": d, "BillFromFirstName": "", "BillFromLastName": "", "BillFromCompanyName": VENDOR_NAME,
        "BillFromAddress2": "", "BillFromPhoneNumber": "", "BillFromEmail": "",
        "VendorAddress": "Not disclosed", "VendorCity": "Vancouver", "VendorState": "BC",
        "VendorCountry": "Canada", "VendorPostalZipCode": "",
        "ShipFromAddress": "Not disclosed", "ShipFromAddress2": "", "ShipFromCity": "Vancouver",
        "ShipFromState": "BC", "ShipFromCountry": "Canada", "ShipFromPostalZipCode": "",
        "ReconcileAccrualAccntName": "", "ReconcileEntityAccntName": "", "UndoReconcilation": False,
        "IsConvertToBill": False, "RestrictVoidExpenseBill": False, "IsMultiReconcilation": False,
        "UndoReconcileLinkedExpenseBills": False, "TaxServiceHashCode": 0, "EntityUseCode": None,
        "TaxAdjItemArrLastValid": list(tax_adj), "TaxAdjItemArr": list(tax_adj), "TotalCBM": 0,
        "BillDate": d, "StoreName": STORE_NAME, "VendorBillNumber": vendor_bill_number(date),
        "ProjectClassId": "", "BuyerId": "",
    }
    return {"billHeader": header, "billItemLineArr": [], "billExpenseLineArr": lines}


def existing_bill(date, total):
    """A DHL Express Canada Bill already posted on ``date`` for ``total`` — checked in
    the GL (the public bill/getbill read only lists open bills, so a paid duplicate
    would slip past it). Returns the GL row (TxnNumber, Memo) or None."""
    x = XoroClient.from_config() if hasattr(XoroClient, "from_config") else XoroClient()
    d = date.isoformat()
    for r in x.get_gl_transactions(d, d, account_gl_codes="2100"):
        if (r.get("TxnTypeName") == "Bill" and r.get("EntityFullName") == VENDOR_NAME
                and abs(round(float(r.get("Amount") or 0), 2)) == round(total, 2)):
            return r
    return None


def main(folder, create=False):
    folder = pathlib.Path(folder)
    totals = read_totals(folder)
    date = statement_date(folder)
    if date is None:
        raise SystemExit(f"No YYYY-MM-DD.pdf statement in {folder} — need it for the bill date")
    try:
        bill = build_bill(totals, date)
    except NothingToBill as e:
        print(e)
        return
    h = bill["billHeader"]
    print(f"DHL Express Canada bill dated {h['TxnDate']}  ({h['VendorBillNumber']})")
    for l in bill["billExpenseLineArr"]:
        print(f"  {l['AccountName']:28} {float(l['Amount']):>10,.2f}")
    for t in h["TaxAdjItemArr"]:
        print(f"  tax adj {t['Name']:20} {t['Amount']:>10,.2f}")
    print(f"  {'TOTAL':28} {h['TotalAmt']:>10,.2f}")

    dup = existing_bill(date, h["TotalAmt"])
    if dup:
        print(f"\nAlready posted: Bill GL txn {dup.get('TxnNumber')} on {h['TxnDate']} for {h['TotalAmt']:,.2f} — nothing created")
        sys.exit(3)  # distinct code so run.sh can skip its "post it?" prompt
    if not create:
        print("\nDry run — pass --create to post it")
        return
    r = WebMethodClient.from_config().call("BillWebMethods", "createNewBill", billJsonObj=json.dumps(bill))
    if not r.get("Result"):
        raise SystemExit(f"createNewBill failed: {r.get('Message')}")
    print(f"\n{r.get('Message')}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)
    main(args[0], create="--create" in sys.argv)
