"""Create Xoro bank deposits from Shopify payouts (gap reconcile).

For a Shopify payout, matches its order numbers to Xoro undeposited payments
(by the undeposited row's ``ChequeNo`` == the Shopify order number), builds a
bank deposit containing those payments plus a Shopify-fee cash-back line, and
lists any unmatched orders in the deposit's memo.

Dry-run by default — prints the exact deposit without creating it.

    python3 shopify_deposits.py 5898.38        # dry-run that payout (June)
"""

import json
import os
import urllib.request

from xoro_webmethods import WebMethodClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
SHOPIFY_API_VERSION = "2026-01"

# Xoro accounts (FAccountingId) keyed by deposit currency.
DEPOSIT_TO_ACCT = {"USD": "B7D04105A81AED1CB3EA3AB9426A"}      # 1140 - Umpqua Bank 1729 (USD)
FEE_ACCT = {
    "USD": "B7D1B02C7EB5CD837D800F3B405B",                    # 7456 - CC Processing Fees (USD)
    "CAD": "B7D04105A81C07FA7E88869F40C7",                    # 7455 - CC Processing Fees
}
FX_ACCT = {
    "USD": "B7E6DB72935ED49A7DA809A1468B",                    # 8151 - Exchange Rate Gain/Loss - USD
    "CAD": "1099",                                            # 8150 - Exchange Rate Gain/Loss (CAD)
}
CURRENCY_ID = {"USD": 1001, "CAD": 1}

# Generic Shopify cash-sale customer used on fee/FX adjustment lines (from a real deposit).
ADJ_ENTITY_ID = "E533E494-F1CB-4F1A-A51E-4C7AA9483208"        # "Cash Sale - Shopify CA"
ADJ_ENTITY_NAME = "Cash Sale - Shopify CA"
ADJ_STORE_ID = 10001


def _adjustment_line(accnt_id, amount, currency_id, memo, line_number):
    """A non-payment deposit line (fee or FX) drawn from a GL account."""
    return {
        "AllowDuplicateThirdPartyRefNo": False, "Amount": round(amount, 2), "BankDepositId": 0,
        "ChequeNo": "", "DeleteFlag": False,
        "DepositFromAccntCurrencyId": currency_id, "DepositFromAccntId": accnt_id,
        "DepositFromAccntTypeId": 0,
        "EntityAccountId": ADJ_ENTITY_ID, "EntityName": ADJ_ENTITY_NAME,
        "EntityTypeId": 10, "EntityTypeName": "customer",
        "Id": 0, "LineNumber": line_number,
        "LinkedFlag": None, "LinkedTxnTableId": 0,
        "Memo": memo, "StoreId": ADJ_STORE_ID, "StoreName": "CA",
    }


def _env(key):
    for line in open(ENV_PATH):
        line = line.strip()
        if line.startswith(key + "="):
            return line.partition("=")[2].strip().strip('"').strip("'")
    return None


def _shopify(path):
    store, token = _env("SHOPIFY_STORE"), _env("SHOPIFY_ADMIN_TOKEN")
    req = urllib.request.Request(
        "https://%s/admin/api/%s/%s" % (store, SHOPIFY_API_VERSION, path),
        headers={"X-Shopify-Access-Token": token, "Accept": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())


def get_payout(amount=None, payout_id=None, date_min=None, date_max=None):
    """Return a payout dict with its orders (order_number, amount, fee, type) + total fees."""
    if payout_id:
        payouts = [_shopify("shopify_payments/payouts/%s.json" % payout_id)["payout"]]
    else:
        q = "shopify_payments/payouts.json?limit=250"
        q += ("&date_min=%s" % date_min) if date_min else ""
        q += ("&date_max=%s" % date_max) if date_max else ""
        payouts = _shopify(q)["payouts"]
    if amount is not None:
        p = next((x for x in payouts if abs(float(x["amount"]) - amount) < 0.005), None)
    else:
        p = payouts[0]
    if not p:
        raise ValueError("payout not found")
    txns = _shopify("shopify_payments/balance/transactions.json?payout_id=%s&limit=250" % p["id"])["transactions"]
    charges = [t for t in txns if t["type"] in ("charge", "refund")]
    ids = [str(t["source_order_id"]) for t in charges if t.get("source_order_id")]
    onum = {}
    if ids:
        got = _shopify("orders.json?ids=%s&status=any&fields=id,order_number&limit=250" % ",".join(ids))["orders"]
        onum = {str(o["id"]): str(o["order_number"]) for o in got}
    orders, fees = [], 0.0
    for t in charges:
        orders.append({"order": onum.get(str(t.get("source_order_id"))),
                       "amount": float(t["amount"]), "fee": float(t["fee"]), "type": t["type"]})
        fees += float(t["fee"])
    return {"id": p["id"], "date": p["date"], "amount": float(p["amount"]),
            "currency": p["currency"], "fees": round(fees, 2), "orders": orders}


def _iso_to_slash(d):
    y, m, dd = d[:10].split("-")
    return "%d/%d/%d" % (int(m), int(dd), int(y))


def build_deposit(payout, undeposited_rows, exchange_rate="1"):
    """Return (bankDepositObj, matched_rows, missing_orders).

    An order can have several undeposited rows with the same ``ChequeNo`` (e.g. an
    original deposit *and* a refund), so match on the **signed amount** — a refund
    (negative Shopify amount) picks the negative row, a charge picks the positive
    one — and never reuse a row.
    """
    from collections import defaultdict
    by_cheque = defaultdict(list)
    for r in undeposited_rows:
        if r.get("ChequeNo") is not None:
            by_cheque[str(r["ChequeNo"])].append(r)
    matched, missing = [], []
    used = set()
    for o in payout["orders"]:
        want_neg = o["amount"] < 0                          # refund -> negative Xoro row
        cands = [r for r in by_cheque.get(str(o["order"]), []) if id(r) not in used]
        # prefer rows with the same sign as the Shopify amount (deposit vs refund),
        # then the closest amount. Sign disambiguates; amounts differ by FX rounding.
        same = [r for r in cands if (float(r["Amount"]) < 0) == want_neg]
        pool = same or cands
        if pool:
            best = min(pool, key=lambda r: abs(float(r["Amount"]) - o["amount"]))
            used.add(id(best))
            r = dict(best)
            r["LinkedFlag"] = True
            r["LineNumber"] = len(matched)
            matched.append(r)
        else:
            missing.append(o["order"])
    cur = payout["currency"]
    cid = CURRENCY_ID[cur]

    # Fee line (negative to the CC-processing-fee GL), like the auto-created deposits.
    matched.append(_adjustment_line(FEE_ACCT[cur], -payout["fees"], cid, "Shopify fees", len(matched)))

    # FX line to balance the deposit exactly to the payout (Xoro amounts differ from
    # Shopify's payout currency by rounding). Goes to the Exchange Rate Gain/Loss GL.
    subtotal = round(sum(float(r["Amount"]) for r in matched), 2)
    fx = round(payout["amount"] - subtotal, 2)
    if abs(fx) >= 0.01:
        matched.append(_adjustment_line(FX_ACCT[cur], fx, cid, "FX rounding", len(matched)))

    header = {
        "Id": -1, "TxnId": None, "TxnNo": -1, "TxnDate": _iso_to_slash(payout["date"]),
        "BankDepositNumber": None,
        "DepositToAccntId": DEPOSIT_TO_ACCT[cur], "DepositToAccntCurrencyId": cid,
        "TotalAmount": 0, "CurrencyCode": cur, "CurrencyId": cid,
        "HomeCurrencyId": 1, "HomeCurrencyName": "CAD", "ExchangeRate": str(exchange_rate),
        "CashBackMemo": "", "CashBackAccntId": "", "CashBackAccntCurrencyId": "",
        "CashBackAccntName": "", "CashBackAmount": 0,
        "Memo": ("missing: " + " ".join(missing)) if missing else "",
    }
    return {"BankDepositHeaderObj": header, "BankDepositDetailArr": matched}, matched, missing


def create_shopify_deposit(amount=None, payout_id=None, date_min=None, date_max=None,
                           client=None, dry_run=True):
    payout = get_payout(amount=amount, payout_id=payout_id, date_min=date_min, date_max=date_max)
    client = client or WebMethodClient.from_config()
    cur = payout["currency"]
    undep = client.get_undeposited_transactions(CURRENCY_ID[cur])
    # Exchange rate only affects the CAD home value; precision isn't critical (any
    # residual lands in the FX account). Use a recent USD->CAD default if unavailable.
    rate = "1.41"
    try:
        hc = (client.get_data_for_bank_deposit() or {}).get("HomeCurrencyObj") or {}
        rate = str(hc.get("ExchangeRate") or hc.get("Rate") or rate)
    except Exception:  # noqa: BLE001
        pass
    obj, matched, missing = build_deposit(payout, undep, exchange_rate=rate)
    det = obj["BankDepositDetailArr"]
    deposit_total = round(sum(float(r["Amount"]) for r in det), 2)
    payment_lines = sum(1 for r in det if r.get("LinkedFlag"))
    fx = next((r["Amount"] for r in det if r.get("DepositFromAccntId") == FX_ACCT[cur]), 0.0)
    summary = {
        "payout": {k: payout[k] for k in ("id", "date", "amount", "currency", "fees")},
        "undeposited_pulled": len(undep),
        "payment_lines": payment_lines, "missing_orders": missing,
        "fee": round(-payout["fees"], 2), "fx_residual": fx,
        "deposit_total": deposit_total,
        "balances": abs(deposit_total - payout["amount"]) < 0.01,
        "deposit_to": obj["BankDepositHeaderObj"]["DepositToAccntId"],
        "exchange_rate": rate, "memo": obj["BankDepositHeaderObj"]["Memo"],
    }
    if dry_run:
        return {"dry_run": True, "summary": summary, "payload": obj}
    return {"created": True, "summary": summary, "deposit": client.create_bank_deposit(obj)}


if __name__ == "__main__":
    import sys
    amt = float(sys.argv[1]) if len(sys.argv) > 1 else 5898.38
    r = create_shopify_deposit(amount=amt, date_min="2026-06-01", date_max="2026-06-30", dry_run=True)
    s = r["summary"]
    p = s["payout"]
    print("=== DRY RUN: Shopify payout -> Xoro bank deposit ===")
    print("payout %s  %s  %.2f %s   Shopify fees %.2f" % (p["id"], p["date"], p["amount"], p["currency"], p["fees"]))
    print("payment lines: %d    missing (-> memo): %s" % (s["payment_lines"], s["missing_orders"] or "none"))
    print("fee line:      %.2f  -> 7456 USD / 7455 CAD" % s["fee"])
    print("FX line:       %+.2f  -> 8151 USD / 8150 CAD" % s["fx_residual"])
    print("DEPOSIT TOTAL: %.2f    payout: %.2f    BALANCES: %s" % (s["deposit_total"], p["amount"], s["balances"]))
    print("deposit to:    %s (Umpqua USD)   exchange rate: %s" % (s["deposit_to"], s["exchange_rate"]))
    print("memo:          %s" % (s["memo"] or "(empty - nothing missing)"))
