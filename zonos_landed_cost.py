#!/usr/bin/env python3
"""
Reproduce Zonos's "landed cost breakdown" export from the API, for the Avalara
UK VAT / EU IOSS filings.

The hand-run version of this is a CSV downloaded from the Zonos dashboard and
dropped next to the month's `AvaTemplate.xlsx` (see
`Vendor Invoices - Trade/Avalara Europe Ltd (UK EU VAT)/FY <fy>/<yy mm>/`). This
builds the same CSV from `api.zonos.com/graphql`, so a period can be regenerated
without the dashboard.

WHICH ROWS MATTER FOR AVALARA: the `processing` column. A row is

  * ``TAX REMITTANCE`` when the order carries a Zonos ``remittance`` — tax
    collected under one of the account's own registrations (``taxIds``: GB HMRC
    GB271143234, FR IOSS IM2500014008), which is what gets filed; or
  * ``CUSTOMS BILL`` when it does not — duty and tax billed at the border on a DDP
    shipment, carrying a DHL advancement fee rather than a remittance.

Only the first kind feeds a VAT return.

Validated against the real dashboard export for 2026-01-01..2026-02-28 with
``--validate``, which regenerates that period and diffs every cell.

COLUMNS NOT DERIVED: ``orderFeeShopper`` is never populated in the export we have,
so no fee type could be matched to it. Any fee type not in ``FEE_COLUMN`` is
reported as a warning rather than silently dropped — it still reaches
``orderTotal``, which comes from the order's own subtotals.

Usage:
    python3 zonos_landed_cost.py 2026-06-01 2026-09-30
    python3 zonos_landed_cost.py 2026-06-01 2026-09-30 --out DIR
    python3 zonos_landed_cost.py --validate
"""

import csv
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
ENDPOINT = "https://api.zonos.com/graphql"
AVALARA_DIR = ("/Users/angus/Library/CloudStorage/OneDrive-St.MoritzWatch/Accounting Docs/"
               "Vendor Invoices - Trade/Avalara Europe Ltd (UK EU VAT)")
ACCOUNT_NAME = "Momentum Watches"

COLUMNS = ["accountName", "orderDate", "orderNumber", "orderReference", "customerName",
           "addressLine1", "addressLine2", "city", "state", "postalCode", "countryCode",
           "trackingNumber", "currency", "orderTax", "orderDuty", "orderFx",
           "orderFeeCarrier", "orderAdditionalTariffLines", "orderFeeAdvancement",
           "orderFeeBrokerage", "landedCostGuarantee", "orderFeeShopper", "orderTotal",
           "processing"]

# Zonos LandedCostFeeType -> the export's fee column. Types absent here are not
# silently dropped: `build_row` reports them.
FEE_COLUMN = {
    "ADVANCEMENT": "orderFeeAdvancement",
    "BROKERAGE_FEE": "orderFeeBrokerage",
    "ADDITIONAL_TARIFF_LINES": "orderAdditionalTariffLines",
    "GUARANTEE_ORDER": "landedCostGuarantee",
    "GUARANTEE_PERCENT": "landedCostGuarantee",
    "DUTY_FX": "orderFx",
    "CURRENCY_CONVERSION_FEE": "orderFx",
    "DDP_SERVICE_FEE": "orderFeeCarrier",      # e.g. "UPS Canada Duty & Tax Forwarding Charge"
}

ORDER_FIELDS = """
  accountOrderNumber zonosOrderId createdAt currencyCode destinationCountryCode
  trackingNumbers
  remittance { amount description taxId }
  parties { type person { firstName lastName companyName }
            location { line1 line2 locality administrativeArea postalCode countryCode } }
  amountSubtotals { items shipping duties taxes fees discounts }
  landedCosts { fees { amount type } }
"""


class ZonosError(RuntimeError):
    pass


def _token():
    for line in open(ENV_PATH):
        if line.strip().startswith("ZONOS_CREDENTIAL_TOKEN="):
            return line.partition("=")[2].strip().strip('"').strip("'")
    raise ZonosError("ZONOS_CREDENTIAL_TOKEN missing from .env")


def gql(query, variables=None):
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Content-Type": "application/json", "credentialToken": _token()},
        method="POST")
    try:
        d = json.load(urllib.request.urlopen(req, timeout=90))
    except urllib.error.HTTPError as e:
        raise ZonosError("HTTP %s: %s" % (e.code, e.read()[:300].decode("utf-8", "replace")))
    if d.get("errors"):
        raise ZonosError("; ".join(e.get("message", str(e)) for e in d["errors"]))
    return d["data"]


def tax_registrations():
    """The account's own tax registrations — what Avalara files for."""
    return gql("{ taxIds { id countryCode taxIdNumber type } }")["taxIds"]


def fetch_orders(start, end):
    """Every order created in [start, end] inclusive, oldest first."""
    q = ("query($f: OrdersFilter, $after: String) { orders(first: 100, after: $after, "
         "filter: $f) { totalCount pageInfo { hasNextPage endCursor } "
         "edges { node { %s } } } }" % ORDER_FIELDS)
    f = {"between": {"after": "%sT00:00:00Z" % start, "before": "%sT23:59:59Z" % end}}
    out, after = [], None
    while True:
        c = gql(q, {"f": f, "after": after})["orders"]
        out.extend(e["node"] for e in c["edges"])
        if not c["pageInfo"]["hasNextPage"]:
            break
        after = c["pageInfo"]["endCursor"]
    out.sort(key=lambda o: (o["createdAt"], o.get("accountOrderNumber") or ""))
    return out


def _money(v):
    """Match the export's formatting: trailing zeros trimmed, blank for nothing/zero."""
    if v in (None, "", 0, 0.0):
        return ""
    d = Decimal(str(v))
    s = format(d.normalize(), "f")
    return s


def _destination(order):
    for p in (order.get("parties") or []):
        if p.get("type") == "DESTINATION":
            return p
    return {}


def build_row(order, warn=None):
    dest = _destination(order)
    person, loc = dest.get("person") or {}, dest.get("location") or {}
    # the export prefers the person's name over companyName -- several records carry a
    # companyName that is just the name re-ordered ("Richter Katalin" for Katalin Richter)
    name = (" ".join(x for x in (person.get("firstName"), person.get("lastName")) if x)
            or person.get("companyName"))
    a = order.get("amountSubtotals") or {}
    # `discounts` arrives already negative, so it is ADDED; subtracting it overstates
    # the total (verified against the dashboard export, order 64768: 652.86 not 668.46)
    total = (Decimal(str(a.get("items") or 0)) + Decimal(str(a.get("shipping") or 0))
             + Decimal(str(a.get("duties") or 0)) + Decimal(str(a.get("taxes") or 0))
             + Decimal(str(a.get("fees") or 0)) + Decimal(str(a.get("discounts") or 0)))
    remittance = order.get("remittance") or []
    row = {c: "" for c in COLUMNS}
    row.update({
        "accountName": ACCOUNT_NAME,
        "orderDate": order["createdAt"][:10],
        "orderNumber": order.get("zonosOrderId") or "",
        "orderReference": order.get("accountOrderNumber") or "",
        "customerName": name or "",
        "addressLine1": loc.get("line1") or "",
        "addressLine2": loc.get("line2") or "",
        "city": loc.get("locality") or "",
        "state": loc.get("administrativeArea") or "",
        "postalCode": loc.get("postalCode") or "",
        "countryCode": loc.get("countryCode") or order.get("destinationCountryCode") or "",
        "trackingNumber": (order.get("trackingNumbers") or [""])[0] or "",
        "currency": order.get("currencyCode") or "",
        "orderTax": _money(a.get("taxes")),
        "orderDuty": _money(a.get("duties")),
        "orderTotal": _money(total),
        "processing": "TAX REMITTANCE" if remittance else "CUSTOMS BILL",
    })
    by_col = {}
    for lc in (order.get("landedCosts") or []):
        for fee in (lc.get("fees") or []):
            col = FEE_COLUMN.get(fee.get("type"))
            if col is None:
                if warn is not None:
                    warn.append("%s: unmapped fee type %s (%s) — not in any column"
                                % (row["orderReference"], fee.get("type"), fee.get("amount")))
                continue
            by_col[col] = by_col.get(col, Decimal(0)) + Decimal(str(fee.get("amount") or 0))
    for col, amount in by_col.items():
        row[col] = _money(amount)
    return row


def build(start, end):
    orders = fetch_orders(start, end)
    warnings = []
    rows = [build_row(o, warnings) for o in orders]
    return rows, warnings


def fiscal_year(d):
    return d.year + 1 if d.month > 7 else d.year


def default_out(end):
    e = date.fromisoformat(end)
    return os.path.join(AVALARA_DIR, "FY %d" % fiscal_year(e),
                        "%02d %02d" % (e.year % 100, e.month))


def write_csv(rows, start, end, out_dir=None):
    folder = out_dir or default_out(end)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "%s-to-%s-zonos-landed-cost-breakdown.csv" % (start, end))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


VALIDATE_FILE = os.path.join(AVALARA_DIR, "FY 2026", "26 02",
                             "2026-01-01-to-2026-02-28-zonos-landed-cost-breakdown.csv")


def validate():
    """Regenerate the real dashboard export and diff it cell by cell."""
    want = list(csv.DictReader(open(VALIDATE_FILE, encoding="utf-8-sig")))
    got, _ = build("2026-01-01", "2026-02-28")
    by_ref = {r["orderReference"]: r for r in got}
    print("dashboard rows %d | API rows %d" % (len(want), len(got)))
    missing = [w["orderReference"] for w in want if w["orderReference"] not in by_ref]
    extra = [g for g in by_ref if g not in {w["orderReference"] for w in want}]
    if missing:
        print("  in export but not from API: %s" % ", ".join(missing))
    if extra:
        print("  from API but not in export: %s" % ", ".join(extra))
    diffs = 0
    for w in want:
        g = by_ref.get(w["orderReference"])
        if not g:
            continue
        for c in COLUMNS:
            a, b = (w.get(c) or "").strip(), (g.get(c) or "").strip()
            if a != b:
                diffs += 1
                if diffs <= 25:
                    print("  %-8s %-26s export=%r api=%r" % (w["orderReference"], c, a, b))
    print("%d cell difference(s)%s" % (diffs, "" if diffs else "  — exact match"))
    return diffs == 0 and not missing and not extra


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
    regs = tax_registrations()
    print("tax registrations: %s" % ", ".join(
        "%s %s (%s)" % (r["countryCode"], r["taxIdNumber"], r["type"]) for r in regs))
    rows, warnings = build(start, end)
    print("%d order(s) %s..%s" % (len(rows), start, end))
    remit = [r for r in rows if r["processing"] == "TAX REMITTANCE"]
    print("  TAX REMITTANCE (reportable): %d" % len(remit))
    print("  CUSTOMS BILL:                %d" % (len(rows) - len(remit)))
    for w in warnings:
        print("  warning: %s" % w)
    path = write_csv(rows, start, end, out)
    print("wrote %s" % path)
    if not remit:
        print("\nNOTE: no TAX REMITTANCE rows, so nothing here feeds the UK VAT / IOSS return.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
