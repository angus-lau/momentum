#!/usr/bin/env python3
"""
PayPal month -> the three Xoro bank deposits (+ the withdrawals that become Fund Transfers).

Usage:
    python3 paypal_deposits.py 2026-08              # dry-run: show what would be booked
    python3 paypal_deposits.py 2026-08 --create     # create the deposits

Reads the month's transactions (the CSV `paypal_statements.py` files, or the API with
``--api``), resolves each sale to its Shopify order and then to a Xoro undeposited
row, and reports the three deposits the documented process calls for:

  service_centre  CAD rows belonging to the Service Centre store -> 1145 (header CAD)
  combined        non-USD rows belonging to the US store         -> 1145 (header USD)
  usd_native      USD rows (no bridging needed)                  -> 1143

The split is by **store**, not by the currency sheet a row appears on, which is why
``custom_field.shop_id`` from the API matters: it says which Shopify store an order
belongs to without guessing or searching both.

Excluded from deposits: ``General Currency Conversion`` rows (FX side-entries whose
total feeds the 1143 statement instead) and ``User Initiated Withdrawal`` rows (each
becomes a Fund Transfer 1143 -> 1140; reported here, not created).

Unmatched transactions get no payment line — they are named in the deposit memo and
their fee is left out of the fee line, exactly as the manual process does.

Dry-run by default.
"""

import os
import sys
import csv
import json
import glob
import calendar
import datetime
import urllib.parse
import urllib.request
from decimal import Decimal
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field

import paypal_statements as pps
from xoro_webmethods import WebMethodClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
SHOPIFY_API_VERSION = "2024-10"

US_STORE = 54087680178          # Momentum Watches (momentum-watch-teifi-digital)
SERVICE_CENTRE = 19541053       # Momentum Watches Service Center (ca-momentumwatch)
STORE_ENV = {US_STORE: ("SHOPIFY_STORE", "SHOPIFY_ADMIN_TOKEN"),
             SERVICE_CENTRE: ("SHOPIFY_STORE_2", "SHOPIFY_ADMIN_TOKEN_2")}

CURRENCY_ID = {"CAD": 1, "USD": 1001, "EUR": 1045, "GBP": 1103, "AUD": 2}
ADJ_STORE_ID, ADJ_STORE_NAME = 10001, "CA"

BRIDGE = {"Id": "B7D1A739F88DCEF8CFF09CA340F8", "Name": "1145 - Temporary Bank Account (CAD)"}
PAYPAL_USD = {"Id": "B7D04105A81AC13AE701924645D2", "Name": "1143 - Paypal USD"}
FEE_CAD = {"Id": "B7D04105A81C07FA7E88869F40C7", "Name": "Credit Card Processing Fees",
           "TypeId": 1034, "CurrencyId": 1, "CurrencyName": "CAD"}
FEE_USD = {"Id": "B7D1B02C7EB5CD837D800F3B405B", "Name": "Credit Card Processing Fees (USD)",
           "TypeId": 1034, "CurrencyId": 1001, "CurrencyName": "USD"}

# deposit key -> (deposit-to account, header currency, fee account)
DEPOSITS = OrderedDict([
    ("service_centre", (BRIDGE, "CAD", FEE_CAD)),
    ("combined", (BRIDGE, "USD", FEE_USD)),
    ("usd_native", (PAYPAL_USD, "USD", FEE_USD)),
])

SALE_DESCRIPTIONS = {"Express Checkout Payment", "Payment Refund"}
CONVERSION = "General Currency Conversion"
WITHDRAWAL = "User Initiated Withdrawal"


@dataclass
class Txn:
    transaction_id: str
    date: datetime.date
    currency: str
    description: str
    gross: Decimal
    fee: Decimal
    invoice_id: str
    shop_id: int = None
    order_number: str = None
    xoro_rows: list = field(default_factory=list)


# ---------- classification ----------

def classify(t):
    """Which deposit a transaction belongs to, or None if it isn't a deposit line.

    Currency decides whether the 1145 bridge is needed (a USD row never is); the
    store decides which side of the bridge it sits on.
    """
    if t.description not in SALE_DESCRIPTIONS:
        return None                       # conversions and withdrawals are handled elsewhere
    if t.currency == "USD":
        return "usd_native"
    if t.shop_id == SERVICE_CENTRE:
        return "service_centre"
    return "combined"


def cheque_candidates(order_number):
    """The ChequeNo forms Xoro may hold for a Shopify order name.

    The Service Centre store names orders "C36337" but Xoro stores the bare
    "36337" (its refs are SC-CD…), so try the name as-is and then without its
    leading letters.
    """
    order = (order_number or "").strip()
    forms = [order]
    stripped = order.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ#")
    if stripped and stripped != order:
        forms.append(stripped)
    return forms


def group_by_order(txns):
    """{order_number: [txn, ...]} — several transactions can share one order."""
    out = defaultdict(list)
    for t in txns:
        if t.order_number:
            out[t.order_number].append(t)
    return dict(out)


def fee_total(txns):
    return sum((t.fee for t in txns), Decimal(0))


def month_end(month):
    year, mon = (int(x) for x in month.split("-"))
    return datetime.date(year, mon, calendar.monthrange(year, mon)[1])


def deposit_memo(key, date, currencies):
    stamp = "%s %d" % (date.strftime("%B"), date.year)
    if key == "service_centre":
        return "PayPal Service Centre Payout - %s" % stamp
    if key == "combined":
        return "PayPal Payout %s - %s" % (", ".join(currencies), stamp)
    return "PayPal Payout USD - %s" % stamp


# ---------- sources ----------

def _env(key):
    for line in open(ENV_PATH):
        line = line.strip()
        if line.startswith(key + "="):
            return line.partition("=")[2].strip().strip('"').strip("'")
    return None


def _money(s):
    s = (s or "0").replace(",", "").strip()
    return Decimal(s or "0")


def load_csv(month):
    """The month's transactions from the CSV paypal_statements.py filed."""
    folder = pps.month_folder(month)
    files = sorted(glob.glob(os.path.join(folder, "*.CSV")))
    if not files:
        raise SystemExit("no CSV in %s — run paypal_statements.py %s first" % (folder, month))
    path = files[-1]
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d, m, y = r["Date"].split("/")
            out.append(Txn(transaction_id=r["Transaction ID"],
                           date=datetime.date(int(y), int(m), int(d)),
                           currency=r["Currency"].strip(),
                           description=r["Description"].strip(),
                           gross=_money(r["Gross "]), fee=_money(r["Fee "]),
                           invoice_id=(r["Invoice ID"] or "").strip()))
    return out, path


def load_api(month):
    txns = pps.fetch_transactions(month)
    out = []
    for t in txns:
        i = t["transaction_info"]
        d, _time = pps.local_date_time(i["transaction_initiation_date"])
        dd, mm, yy = d.split("/")
        out.append(Txn(transaction_id=i["transaction_id"],
                       date=datetime.date(int(yy), int(mm), int(dd)),
                       currency=i["transaction_amount"]["currency_code"],
                       description=pps.description_for(i.get("transaction_event_code")),
                       gross=Decimal(str(i["transaction_amount"]["value"])),
                       fee=Decimal(str((i.get("fee_amount") or {}).get("value", "0") or "0")),
                       invoice_id=i.get("invoice_id") or ""))
    return out, "API"


def shop_ids(month):
    """{transaction_id: shop_id} — only the API carries this."""
    out = {}
    for t in pps.fetch_transactions(month):
        i = t["transaction_info"]
        cf = i.get("custom_field") or ""
        if cf.startswith("{"):
            try:
                out[i["transaction_id"]] = json.loads(cf).get("shop_id")
            except ValueError:
                pass
    return out


def shopify_orders(tokens, shop_id):
    """{invoice_id: order_number} for one store, one search per token."""
    se, te = STORE_ENV[shop_id]
    domain, tok = _env(se), _env(te)
    out = {}
    for t in tokens:
        q = '{ orders(first: 2, query: "%s") { edges { node { name } } } }' % t
        req = urllib.request.Request(
            "https://%s/admin/api/%s/graphql.json" % (domain, SHOPIFY_API_VERSION),
            data=json.dumps({"query": q}).encode(),
            headers={"X-Shopify-Access-Token": tok, "Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        edges = ((data.get("data") or {}).get("orders") or {}).get("edges") or []
        out[t] = edges[0]["node"]["name"] if edges else None
    return out


# ---------- report ----------

def build(month, use_api=False):
    txns, source = (load_api(month) if use_api else load_csv(month))
    print("source: %s — %d transaction(s)" % (source, len(txns)))

    ids = shop_ids(month)
    for t in txns:
        t.shop_id = ids.get(t.transaction_id)

    # resolve order tokens per store
    by_store = defaultdict(set)
    for t in txns:
        if t.description in SALE_DESCRIPTIONS and t.invoice_id:
            by_store[t.shop_id or US_STORE].add(t.invoice_id)
    resolved = {}
    for shop, tokens in by_store.items():
        if shop in STORE_ENV:
            resolved.update(shopify_orders(sorted(tokens), shop))
    for t in txns:
        t.order_number = resolved.get(t.invoice_id)

    # Undeposited rows sit in the *deposit's* currency, not the currency the customer
    # paid in: a US-store order is USD in Xoro even when PayPal took GBP for it, and
    # Service Centre orders are CAD.
    client = WebMethodClient.from_config()
    undeposited = {}
    for key in DEPOSITS:
        header_cur = DEPOSITS[key][1]
        if header_cur not in undeposited:
            undeposited[header_cur] = client.get_undeposited_transactions(CURRENCY_ID[header_cur])

    date = month_end(month)
    report = OrderedDict()
    for key in DEPOSITS:
        rows = [t for t in txns if classify(t) == key]
        if not rows:
            continue
        acct, header_cur, fee_acct = DEPOSITS[key]
        groups = group_by_order(rows)
        by_cheque = defaultdict(list)
        for r in undeposited.get(header_cur, []):
            by_cheque[str(r.get("ChequeNo")).strip()].append(r)
        matched, missing = [], []
        for order, ts in sorted(groups.items()):
            pool = next((by_cheque[f] for f in cheque_candidates(order) if by_cheque.get(f)), [])
            if pool:
                matched.append((order, ts, pool))
            else:
                missing.append("order %s (txn %s)" % (order, ", ".join(t.transaction_id for t in ts)))
        for t in rows:
            if not t.order_number:
                missing.append("txn %s (%s %s, no Shopify order)" % (t.transaction_id, t.currency, t.gross))
        matched_txns = [t for _o, ts, _p in matched for t in ts]
        report[key] = {
            "account": acct, "header_currency": header_cur, "fee_account": fee_acct,
            "date": date, "rows": rows, "matched": matched, "missing": missing,
            "fee": fee_total(matched_txns),
            "currencies": sorted({t.currency for t in rows}),
        }

    withdrawals = [t for t in txns if t.description == WITHDRAWAL]
    conversions = [t for t in txns if t.description == CONVERSION]
    return report, withdrawals, conversions, txns


def main(month, use_api=False, create=False):
    report, withdrawals, conversions, txns = build(month, use_api=use_api)
    for key, r in report.items():
        print("\n=== %s → %s (header %s) ===" % (key, r["account"]["Name"], r["header_currency"]))
        print("    memo: %s" % deposit_memo(key, r["date"], r["currencies"]))
        lines = 0
        total = Decimal(0)
        for order, ts, pool in r["matched"]:
            for row in pool:
                lines += 1
                total += Decimal(str(row["Amount"]))
                print("      order %-8s %10s  %s" % (order, row["Amount"], row.get("TxnTypeName", "")))
        print("      %d payment line(s), sum %s %s" % (lines, total, r["header_currency"]))
        print("      fee line: %s to %s%s" % (r["fee"], r["fee_account"]["Name"],
              "  (added last, per process)" if key == "combined" else ""))
        if r["missing"]:
            print("      MISSING (%d): %s" % (len(r["missing"]), "; ".join(r["missing"][:6])))
            if len(r["missing"]) > 6:
                print("        ... and %d more" % (len(r["missing"]) - 6))

    print("\n=== withdrawals → Fund Transfers 1143 → 1140 ===")
    for t in withdrawals:
        print("    %s  %s %s" % (t.date, t.currency, t.gross))
    print("    %d transfer(s), total %s" % (len(withdrawals), sum((t.gross for t in withdrawals), Decimal(0))))

    conv_total = sum((t.gross for t in conversions if t.currency == "USD"), Decimal(0))
    print("\n=== 1143 statement inputs ===")
    print("    General Currency Conversion rows: %d (USD side total %s)" % (len(conversions), conv_total))
    try:
        bal = pps.month_end_balances(month)
        print("    PayPal month-end balance: %s" % ", ".join("%s %s" % (c, v) for c, v in bal.items() if v))
    except Exception as e:                                      # noqa: BLE001
        print("    (balance lookup failed: %s)" % e)

    if not create:
        print("\nDry run — nothing created.")
        return
    raise SystemExit("--create is not implemented yet: confirm the dry-run output first")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)
    main(args[0], use_api="--api" in sys.argv, create="--create" in sys.argv)
