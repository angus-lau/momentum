#!/usr/bin/env python3
"""Pull a month of PayPal activity via the API and file it as PayPal's own CSV.

Usage:
    python3 paypal_statements.py 2026-08              # write into the FY folder
    python3 paypal_statements.py 2026-08 --stdout     # print, write nothing
    python3 paypal_statements.py 2026-08 --out DIR    # write somewhere else

Replaces the manual "download the monthly CSV from PayPal's reports UI" step.
Reads ``/v1/reporting/transactions`` and writes the same 18 columns PayPal's own
export uses, into::

    Bank Reconciliations/FY{fy}/Paypal/{YY MM}/GH4H22C7H8HBL-CSR-{start}-{end}-{now}.CSV

Verified against a real PayPal export for August 2026: same 145 transactions,
identical gross/fee/currency/balance/invoice id on every one.

**There is no PDF.** PayPal's REST API exposes only ``/v1/reporting/transactions``
and ``/v1/reporting/balances`` for reporting — every statement/document endpoint
404s and the transactions endpoint refuses ``Accept: application/pdf`` (406). The
monthly statement PDF can only come from the web reports UI.

Balances: ``/v1/reporting/balances?as_of_time=...`` gives the month-end balance per
currency (what the 1143 reconciliation needs) — see ``month_end_balances``.

Auth: PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET in .env (live credentials; the app
needs the ``reporting/search/read`` scope). Stdlib only.
"""

import os
import sys
import csv
import json
import base64
import calendar
import datetime
import urllib.parse
import urllib.request
from decimal import Decimal
from zoneinfo import ZoneInfo

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")

API_BASE = "https://api-m.paypal.com"
ONEDRIVE_BASE = ("/Users/angus/Library/CloudStorage/OneDrive-St.MoritzWatch/"
                 "Accounting Docs/Bank Reconciliations")
ACCOUNT_FOLDER = "Paypal"
REPORT_TZ = "America/Vancouver"      # the Time Zone column PayPal's own export uses
PRIMARY_CURRENCY = "USD"             # the account's primary balance; its block leads the export
FISCAL_YEAR_END_MONTH = 7            # FY ends July 31

# PayPal's export header, spelling and trailing spaces included.
CSV_COLUMNS = ["Date", "Time", "Time Zone", "Description", "Currency", "Gross ", "Fee ",
               "Net", "Balance", "Transaction ID", "From Email Address", "Name",
               "Bank Name", "Bank Account", "Shipping and Handling Amount", "Sales Tax",
               "Invoice ID", "Reference Txn ID"]

# Only the codes this account actually produces are mapped; anything else is passed
# through as the raw code so a new transaction type is visible rather than mislabelled.
EVENT_DESCRIPTIONS = {
    "T0006": "Express Checkout Payment",
    "T0200": "General Currency Conversion",
    "T0403": "User Initiated Withdrawal",
    "T1107": "Payment Refund",
}

# PayPal's export fills "Reference Txn ID" only for transactions derived from an
# earlier one (a conversion or a refund). An Express Checkout Payment carries a
# paypal_reference_id too, but it points at the order, and the export leaves the
# column blank — so mirror that rather than copying the field blindly.
REFERENCES_PARENT = {"T0200", "T1107"}


def description_for(code):
    return EVENT_DESCRIPTIONS.get(code, code or "")


def amount(value):
    """PayPal's CSV money format: 2 dp with thousands separators ('-4,500.00')."""
    if value in (None, ""):
        return "0.00"
    return "{:,.2f}".format(Decimal(str(value)))


def local_date_time(iso_utc):
    """UTC timestamp -> ('DD/MM/YYYY', 'HH:MM:SS') in the report timezone."""
    dt = datetime.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    local = dt.astimezone(ZoneInfo(REPORT_TZ))
    return local.strftime("%d/%m/%Y"), local.strftime("%H:%M:%S")


def month_bounds(month):
    """'YYYY-MM' -> the API's inclusive start/end timestamps for that month."""
    year, mon = (int(x) for x in month.split("-"))
    last = calendar.monthrange(year, mon)[1]
    return ("%04d-%02d-01T00:00:00-0000" % (year, mon),
            "%04d-%02d-%02dT23:59:59-0000" % (year, mon, last))


def fiscal_year_folder(year, month):
    """FY2027 for 2026-08 .. 2027-07."""
    return "FY%d" % (year + 1 if month > FISCAL_YEAR_END_MONTH else year)


def report_filename(account_id, month, now=None):
    """PayPal's own report naming, so the file looks like the ones downloaded by hand."""
    now = now or datetime.datetime.now()
    start, end = month_bounds(month)
    fmt = lambda s: s[:19].replace("-", "").replace("T", "").replace(":", "")  # noqa: E731
    return "%s-CSR-%s-%s-%s.CSV" % (account_id, fmt(start), fmt(end), now.strftime("%Y%m%d%H%M%S"))


def rows_for(txns, primary_currency=None):
    """All transactions as PayPal-CSV rows, ordered and enriched the way the export is.

    A derived row (currency conversion / refund) carries the parent payment's
    shipping amount in PayPal's own export, but the API only puts ``shipping_amount``
    on the parent — so look it up through ``paypal_reference_id``.

    PayPal's export groups rows by currency (the account's primary currency first,
    then the rest alphabetically), each block oldest-first — which is why the
    reconciliation splits into one sheet per currency.
    """
    primary = primary_currency or PRIMARY_CURRENCY
    ids = {t["transaction_info"]["transaction_id"] for t in txns}
    shipping = {t["transaction_info"]["transaction_id"]:
                (t["transaction_info"].get("shipping_amount") or {}).get("value")
                for t in txns}
    rows = []
    for t in txns:
        i = t["transaction_info"]
        inherited = None
        if i.get("transaction_event_code") in REFERENCES_PARENT and not i.get("shipping_amount"):
            inherited = shipping.get(i.get("paypal_reference_id"))
        row = row_for(t, inherited_shipping=inherited)
        # when rows share a timestamp the parent comes first, so a row that references
        # another transaction in this month sorts after it
        row["_derived"] = i.get("paypal_reference_id") in ids
        rows.append(row)
    rows.sort(key=lambda r: (r["Currency"] != primary, r["Currency"],
                             r["Date"].split("/")[::-1], r["Time"], r["_derived"],
                             r["Transaction ID"]))
    for r in rows:
        del r["_derived"]
    return rows


def row_for(txn, inherited_shipping=None):
    """One API transaction -> one PayPal-CSV row."""
    i = txn.get("transaction_info", {})
    payer = txn.get("payer_info") or {}
    date, time = local_date_time(i["transaction_initiation_date"])
    gross = Decimal(str(i.get("transaction_amount", {}).get("value", "0")))
    fee = Decimal(str((i.get("fee_amount") or {}).get("value", "0") or "0"))
    code = i.get("transaction_event_code")
    # the export reports sales tax with the opposite sign to the API
    tax = Decimal(str((i.get("sales_tax_amount") or {}).get("value", "0") or "0"))
    return {
        "Date": date,
        "Time": time,
        "Time Zone": REPORT_TZ,
        "Description": description_for(i.get("transaction_event_code")),
        "Currency": i.get("transaction_amount", {}).get("currency_code", ""),
        "Gross ": amount(gross),
        "Fee ": amount(fee),
        "Net": amount(gross + fee),          # PayPal's fee is already signed negative
        "Balance": amount((i.get("ending_balance") or {}).get("value")),
        "Transaction ID": i.get("transaction_id", ""),
        "From Email Address": payer.get("email_address", "") or "",
        "Name": (payer.get("payer_name") or {}).get("alternate_full_name", "") or "",
        "Bank Name": "",                     # never populated in PayPal's own export
        "Bank Account": "",
        "Shipping and Handling Amount": amount((i.get("shipping_amount") or {}).get("value")
                                                or inherited_shipping),
        "Sales Tax": amount(-tax),
        "Invoice ID": i.get("invoice_id", "") or "",
        "Reference Txn ID": (i.get("paypal_reference_id") or "") if code in REFERENCES_PARENT else "",
    }


# ---------- API ----------

def _env(key, env_path=ENV_PATH):
    if not os.path.exists(env_path):
        return None
    for line in open(env_path):
        line = line.strip()
        if line.startswith(key + "="):
            return line.partition("=")[2].strip().strip('"').strip("'")
    return None


def access_token():
    cid, secret = _env("PAYPAL_CLIENT_ID"), _env("PAYPAL_CLIENT_SECRET")
    if not cid or not secret:
        raise SystemExit("missing PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET in .env")
    req = urllib.request.Request(
        API_BASE + "/v1/oauth2/token", data=b"grant_type=client_credentials",
        headers={"Authorization": "Basic " + base64.b64encode(("%s:%s" % (cid, secret)).encode()).decode(),
                 "Content-Type": "application/x-www-form-urlencoded"})
    return json.load(urllib.request.urlopen(req, timeout=30))["access_token"]


def _get(path, token):
    req = urllib.request.Request(API_BASE + path, headers={"Authorization": "Bearer " + token})
    return json.load(urllib.request.urlopen(req, timeout=90))


def fetch_transactions(month, token=None):
    """Every transaction in the month, following pagination."""
    token = token or access_token()
    start, end = month_bounds(month)
    out, page, pages = [], 1, 1
    while page <= pages:
        qs = urllib.parse.urlencode({"start_date": start, "end_date": end,
                                     "fields": "all", "page_size": "500", "page": str(page)})
        data = _get("/v1/reporting/transactions?" + qs, token)
        out += data.get("transaction_details", [])
        pages = data.get("total_pages") or 1
        page += 1
    return out


def month_end_balances(month, token=None):
    """{currency: Decimal} as of the month's last second — the 1143 rec's ending balance."""
    token = token or access_token()
    _start, end = month_bounds(month)
    data = _get("/v1/reporting/balances?" + urllib.parse.urlencode({"as_of_time": end}), token)
    return {b["currency"]: Decimal(str(b.get("total_balance", {}).get("value", "0")))
            for b in data.get("balances", [])}


def account_id(token=None):
    token = token or access_token()
    return _get("/v1/reporting/balances?" + urllib.parse.urlencode(
        {"as_of_time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S-0000")}),
        token).get("account_id", "PAYPAL")


def month_folder(month):
    year, mon = (int(x) for x in month.split("-"))
    return os.path.join(ONEDRIVE_BASE, fiscal_year_folder(year, mon), ACCOUNT_FOLDER,
                        "%02d %02d" % (year % 100, mon))


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(month, out_dir=None, to_stdout=False):
    token = access_token()
    txns = fetch_transactions(month, token)
    rows = rows_for(txns)
    print("%s: %d transaction(s)" % (month, len(rows)))
    if to_stdout:
        w = csv.DictWriter(sys.stdout, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in rows:
            w.writerow(r)
        return
    folder = out_dir or month_folder(month)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, report_filename(account_id(token), month))
    write_csv(rows, path)
    print("wrote %s" % path)
    bal = month_end_balances(month, token)
    nonzero = {c: v for c, v in bal.items() if v}
    print("month-end balance: %s" % (", ".join("%s %s" % (c, v) for c, v in nonzero.items()) or "0"))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 1:
        print(__doc__)
        sys.exit(1)
    out = None
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    main(args[0], out_dir=out, to_stdout="--stdout" in sys.argv)
