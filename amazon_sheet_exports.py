#!/usr/bin/env python3
"""
Load the three Xoro exports the Amazon payout workbook needs, for a date range.

Usage:
    python3 amazon_sheet_exports.py 08/25/2026 10/10/2026            # pull + write
    python3 amazon_sheet_exports.py 08/25/2026 10/10/2026 --dry-run  # pull only, print counts

Pulls Xoro's Refund, Customer Deposit and Sales Order Centres (the same rows their
"export" button gives) through the logged-in debug Chrome, then replaces the data
rows of the matching [PASTE] tabs in "Amazon Payment Reconciliation - Teifi Fix"
via the Sheets API. Header rows are kept. The user picks the range: the working
month plus a buffer either side.

Needs: debug Chrome logged into Xoro (``xoro_cdp/``), and GOOGLE_CLIENT_ID /
GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN in .env (``gsheet_oauth.py``).
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SHEET_ID = "1ZgFZjCBEsbEmjBBUCUDo8Mzf2HIkzu5opJ4UWvdHzYc"
XORO_TAB = "momentum.xoro.one/"          # any logged-in Xoro tab will do


def txt(v):
    if v is None or v == "":
        return "-"
    s = str(v)
    return "'" + s if s[:1] in "=+@" else s      # never let text become a formula


def num(v):
    return 0 if v in (None, "") else float(v)


def day(v):
    return str(v)[:10]


# (tab, centre compCode, date field, last column, row builder) — columns mirror each Centre's export
EXPORTS = [
    ("Xoro Refunds [PASTE]", 51, "TXN_DATE", "K",
     lambda r: [txt(r["REF_NO"]), txt(r["STORE_NAME"]), day(r["TXN_DATE"]), txt(r["REFUND_TYPE_NAME"]),
                txt(r["REFUND_REF_NUMBER"]), txt(r["REFUND_NUMBER"]), txt(r["PAYMENT_METHOD_TYPE_NAME"]),
                txt(r["CUSTOMER_NAME"]), txt(r["CURRENCY_CODE"]), num(r["AMOUNT"]), txt(r.get("ACH_REFUND_STATUS_NAME"))]),
    ("Xoro Customer Deposits [PASTE]", 42, "TXN_DATE", "H",
     lambda r: [txt(r["REF_NO_2"]), txt(r["SO_NUMBER"]), day(r["TXN_DATE"]), num(r["AMOUNT"]),
                num(r["AMOUNT_REMAINING"]), num(r["AMOUNT_APPLIED"]), num(r["AMOUNT_REFUNDED"]), txt(r["CURRENCY_CODE"])]),
    ("Xoro Sales Order [PASTE]", 19, "DATE_ORDER", "L",
     lambda r: [day(r["DATE_ORDER"]), txt(r["ORDER_NUMBER"]), txt(r["THIRD_PARTY_REF_NAME"]), txt(r["SALE_STORE_NAME"]),
                txt(r["STORE_NAME"]), txt(r["CUSTOMER_NAME"]), txt(r["STATUS_NAME"]), txt(r["ALLOCATION_STATUS_NAME"]),
                num(r["TOTAL_AMOUNT"]), num(r["TOTAL_TAX_AMOUNT"]), txt(r["THIRD_PARTY_DISPLAY_NAME"]), txt(r["THIRD_PARTY_NOTES"])]),
]


def pull(comp, field, start, end):
    """Every Centre row with ``start <= field <= end`` (MM/DD/YYYY), via xoro_cdp."""
    js = open(os.path.join(HERE, "xoro_cdp", "centre_export.js")).read()
    js = (js.replace("__COMP__", str(comp)).replace("__DATEFIELD__", field)
            .replace("__FROM__", start).replace("__TO__", end))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
    out = subprocess.run(["node", os.path.join(HERE, "xoro_cdp", "cdp_eval.mjs"), XORO_TAB, f.name],
                         capture_output=True, text=True, timeout=600)
    os.unlink(f.name)
    try:
        d = json.loads(out.stdout)
    except ValueError:
        raise SystemExit("Xoro pull failed (is the debug Chrome logged into Xoro?):\n" + out.stdout + out.stderr)
    if "rows" not in d:
        raise SystemExit("Xoro pull failed: %s" % str(d)[:300])
    if d["count"] != d["total"]:
        raise SystemExit("Xoro returned %s of %s rows — refusing to write a partial export" % (d["count"], d["total"]))
    return d["rows"]


def sheets_token():
    env = dict(l.strip().split("=", 1) for l in open(os.path.join(HERE, ".env")) if "=" in l and not l.startswith("#"))
    data = urllib.parse.urlencode({"client_id": env["GOOGLE_CLIENT_ID"], "client_secret": env["GOOGLE_CLIENT_SECRET"],
                                   "refresh_token": env["GOOGLE_REFRESH_TOKEN"], "grant_type": "refresh_token"}).encode()
    return json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data))["access_token"]


def sheets(token, method, path, body=None):
    req = urllib.request.Request("https://sheets.googleapis.com/v4/spreadsheets/%s/%s" % (SHEET_ID, path), method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def newest_first(field):
    def key(r):
        m, d, y = day(r[field]).split("/")
        return (y, m, d, r.get("ID", 0))
    return key


def main(start, end, dry_run=False):
    token = None if dry_run else sheets_token()
    for tab, comp, field, last, build in EXPORTS:
        rows = sorted(pull(comp, field, start, end), key=newest_first(field), reverse=True)
        values = [build(r) for r in rows]
        if dry_run:
            print("%-32s %5d rows (dry run)" % (tab, len(values)))
            continue
        sheets(token, "POST", "values/%s:clear" % urllib.parse.quote("'%s'!A2:%s" % (tab, last)), {})
        if values:
            sheets(token, "PUT", "values/%s?valueInputOption=USER_ENTERED"
                   % urllib.parse.quote("'%s'!A2:%s%d" % (tab, last, len(values) + 1)), {"values": values})
        print("%-32s %5d rows written (%s - %s)" % (tab, len(values), start, end))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        sys.exit(1)
    main(args[0], args[1], dry_run="--dry-run" in sys.argv)
