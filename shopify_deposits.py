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

# ---- Per-store configuration -----------------------------------------------
# We run this reconcile for MORE THAN ONE Shopify store, and each store deposits
# into its OWN Xoro accounts. A store maps its payout currency ->
#   deposit : the bank account the deposit lands in
#   fee     : CC-processing-fee account (also used for small CC adjustments)
#   fx      : exchange-rate gain/loss account (FX rounding)
# Every account carries id + display Name/TypeId/Currency so the deposit line
# resolves to a *named* account in Xoro (a line with only an id renders blank in
# the UI). Look ids up from AccountingWebMethods.getAllAccountsForApi (FAccountingId
# = Id, FAccountTypeId = TypeId) when adding a store.
STORES = {
    "momentum": {  # momentum-watch-teifi-digital  (USD payouts)
        "label": "Momentum Watch (US)",
        "store_env": "SHOPIFY_STORE",          # .env key holding the *.myshopify.com domain
        "token_env": "SHOPIFY_ADMIN_TOKEN",    # .env key holding the shpat_ Admin API token
        "accounts": {
            "USD": {
                "deposit": {"Id": "B7D04105A81AED1CB3EA3AB9426A", "Name": "Umpqua Bank 1729 (USD)",
                            "CurrencyId": 1001, "CurrencyName": "USD"},                              # 1140
                "fee":     {"Id": "B7D1B02C7EB5CD837D800F3B405B", "Name": "Credit Card Processing Fees (USD)",
                            "TypeId": 1034, "CurrencyId": 1001, "CurrencyName": "USD"},              # 7456
                "fx":      {"Id": "B7E6DB72935ED49A7DA809A1468B", "Name": "Exchange Rate Gain/Loss - USD",
                            "TypeId": 1016, "CurrencyId": 1001, "CurrencyName": "USD"},              # 8151
            },
        },
    },
    "service_center": {  # ca-momentumwatch  "Momentum Watches Service Center" (CAD payouts)
        "label": "Momentum Watches Service Center (CAD)",
        "store_env": "SHOPIFY_STORE_2",        # ca-momentumwatch.myshopify.com
        "token_env": "SHOPIFY_ADMIN_TOKEN_2",  # shpat_ token (same app, minted via `shopify_oauth.py _2`)
        "accounts": {
            "CAD": {
                "deposit": {"Id": "72FF68D10C373530638D3162C4127", "Name": "BMO 41547651 (CAD)",
                            "CurrencyId": 1, "CurrencyName": "CAD"},                                 # 1160
                "fee":     {"Id": "B7D04105A81C07FA7E88869F40C7", "Name": "Credit Card Processing Fees",
                            "TypeId": 1034, "CurrencyId": 1, "CurrencyName": "CAD"},                 # 7455
                "fx":      {"Id": "1099", "Name": "Exchange Rate Gain/Loss",
                            "TypeId": 1016, "CurrencyId": 1, "CurrencyName": "CAD"},                 # 8150
            },
        },
    },
}
DEFAULT_STORE = "momentum"

CURRENCY_ID = {"USD": 1001, "CAD": 1}
ADJ_STORE_ID = 10001


def _store_accounts(store, currency):
    """The {deposit, fee, fx} account set for ``store``'s ``currency`` payouts."""
    cfg = STORES.get(store)
    if not cfg:
        raise KeyError("unknown store %r; configured: %s" % (store, list(STORES)))
    accts = cfg["accounts"].get(currency)
    if not accts:
        raise KeyError("store %r has no accounts configured for %s payouts" % (store, currency))
    return accts


def _adjustment_line(acct, amount, memo, line_number, txn_date):
    """A non-payment deposit line (fee or FX) drawn from GL account ``acct``.

    ``acct`` is a FEE_ACCOUNT/FX_ACCOUNT entry (id + display name/type/currency),
    so the line shows its named account in Xoro. No entity — these are straight GL
    postings. ``txn_date`` (slash format) is the payout date so every adjustment
    sits on the same date as the payout.
    """
    return {
        "AllowDuplicateThirdPartyRefNo": False, "Amount": round(amount, 2), "BankDepositId": 0,
        "ChequeNo": "", "DeleteFlag": False,
        "DepositFromAccntCurrencyId": acct["CurrencyId"], "DepositFromAccntCurrencyName": acct["CurrencyName"],
        "DepositFromAccntId": acct["Id"], "DepositFromAccntName": acct["Name"],
        "DepositFromAccntTypeId": acct["TypeId"],
        "EntityAccountId": "", "EntityName": "", "EntityTypeId": 0, "EntityTypeName": "",
        "Id": 0, "LineNumber": line_number,
        "LinkedFlag": None, "LinkedTxnTableId": 0,
        "TxnDate": txn_date, "LinkedTxnDate": txn_date,
        "Memo": memo, "StoreId": ADJ_STORE_ID, "StoreName": "CA",
    }


def _env(key):
    for line in open(ENV_PATH):
        line = line.strip()
        if line.startswith(key + "="):
            return line.partition("=")[2].strip().strip('"').strip("'")
    return None


def _shopify(path, store=DEFAULT_STORE):
    cfg = STORES.get(store) or {}
    domain = _env(cfg.get("store_env", "SHOPIFY_STORE"))
    token = _env(cfg.get("token_env", "SHOPIFY_ADMIN_TOKEN"))
    if not domain or not token:
        raise RuntimeError("missing Shopify creds for store %r (set %s / %s in .env)"
                           % (store, cfg.get("store_env"), cfg.get("token_env")))
    req = urllib.request.Request(
        "https://%s/admin/api/%s/%s" % (domain, SHOPIFY_API_VERSION, path),
        headers={"X-Shopify-Access-Token": token, "Accept": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())


def get_payout(amount=None, payout_id=None, date_min=None, date_max=None, store=DEFAULT_STORE):
    """Return a payout dict with its orders (order_number, amount, fee, type) + total fees."""
    if payout_id:
        payouts = [_shopify("shopify_payments/payouts/%s.json" % payout_id, store)["payout"]]
    else:
        q = "shopify_payments/payouts.json?limit=250"
        q += ("&date_min=%s" % date_min) if date_min else ""
        q += ("&date_max=%s" % date_max) if date_max else ""
        payouts = _shopify(q, store)["payouts"]
    if amount is not None:
        p = next((x for x in payouts if abs(float(x["amount"]) - amount) < 0.005), None)
    else:
        p = payouts[0]
    if not p:
        raise ValueError("payout not found")
    txns = _shopify("shopify_payments/balance/transactions.json?payout_id=%s&limit=250" % p["id"], store)["transactions"]
    # resolve order numbers (some reference deleted orders -> stay None)
    ids = list(dict.fromkeys(str(t["source_order_id"]) for t in txns if t.get("source_order_id")))
    onum = {}
    if ids:
        got = _shopify("orders.json?ids=%s&status=any&fields=id,order_number&limit=250" % ",".join(ids), store)["orders"]
        onum = {str(o["id"]): str(o["order_number"]) for o in got}
    orders, adjustments, fees = [], [], 0.0
    for t in txns:
        typ = t["type"]
        if typ == "payout":          # the payout line itself, not a deposit item
            continue
        amt, fee = float(t["amount"]), float(t["fee"])
        fees += fee
        onm = onum.get(str(t.get("source_order_id"))) if t.get("source_order_id") else None
        if typ in ("charge", "refund"):
            orders.append({"order": onm, "amount": amt, "type": typ})
        else:                        # debit / credit / adjustment / dispute ... -> adjustment
            adjustments.append({"amount": amt, "type": typ, "order": onm})
    return {"id": p["id"], "date": p["date"], "amount": float(p["amount"]),
            "currency": p["currency"], "fees": round(fees, 2),
            "orders": orders, "adjustments": adjustments}


def _iso_to_slash(d):
    y, m, dd = d[:10].split("-")
    return "%d/%d/%d" % (int(m), int(dd), int(y))


def build_deposit(payout, undeposited_rows, exchange_rate="1", store=DEFAULT_STORE):
    """Return (bankDepositObj, matched_rows, missing_orders).

    An order can have several undeposited rows with the same ``ChequeNo`` (e.g. an
    original deposit *and* a refund), so match on the **signed amount** — a refund
    (negative Shopify amount) picks the negative row, a charge picks the positive
    one — and never reuse a row. Deposit/fee/FX accounts come from ``store``'s config.
    """
    from collections import defaultdict
    by_cheque = defaultdict(list)
    for r in undeposited_rows:
        if r.get("ChequeNo") is not None:
            by_cheque[str(r["ChequeNo"])].append(r)
    matched, missing = [], []
    used = set()
    matched_shopify = 0.0   # sum of Shopify amounts for the orders we actually matched
    for o in payout["orders"]:
        if o["order"] is None:                              # payout-level txn, no order #
            missing.append("no-order#(%s %.2f)" % (o["type"], o["amount"]))
            continue
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
            matched_shopify += o["amount"]
        else:
            missing.append(str(o["order"]))
    cur = payout["currency"]
    cid = CURRENCY_ID[cur]
    accts = _store_accounts(store, cur)
    pdate = _iso_to_slash(payout["date"])   # adjustments all sit on the payout date

    # Adjustments (debit/credit/etc.): small ones (|amount| < 50) book to the CC
    # adjustments GL (same account as the fee line); larger ones go to the memo for
    # manual handling.
    for a in payout.get("adjustments", []):
        if abs(a["amount"]) < 50:
            matched.append(_adjustment_line(accts["fee"], a["amount"], "CC adjustment", len(matched), pdate))
        else:
            missing.append("%s-adj(%.2f)" % (a["type"], a["amount"]))

    # Fee line (negative to the CC-processing-fee GL), like the auto-created deposits.
    matched.append(_adjustment_line(accts["fee"], -payout["fees"], "Shopify fees", len(matched), pdate))

    # FX line = rounding on the MATCHED orders only (Shopify basis - Xoro basis). It does
    # NOT absorb unmatched / no-order amounts -- when orders are missing the deposit is
    # legitimately short of the payout (surfaced by the caller as not-balancing).
    matched_xoro = round(sum(float(r["Amount"]) for r in matched if r.get("LinkedFlag")), 2)
    fx = round(matched_shopify - matched_xoro, 2)
    if abs(fx) >= 0.01:
        matched.append(_adjustment_line(accts["fx"], fx, "FX rounding", len(matched), pdate))

    bank = accts["deposit"]
    header = {
        "Id": -1, "TxnId": None, "TxnNo": -1, "TxnDate": _iso_to_slash(payout["date"]),
        "BankDepositNumber": None,
        "DepositToAccntId": bank["Id"], "DepositToAccntName": bank["Name"],
        "DepositToAccntCurrencyId": cid,
        "TotalAmount": 0, "CurrencyCode": cur, "CurrencyId": cid,
        "HomeCurrencyId": 1, "HomeCurrencyName": "CAD", "ExchangeRate": str(exchange_rate),
        "CashBackMemo": "", "CashBackAccntId": "", "CashBackAccntCurrencyId": "",
        "CashBackAccntName": "", "CashBackAmount": 0,
        # Anything unmatched (deleted/no-order charges, unfound orders, big adjustments)
        # means the deposit is short of the payout -> flag ERROR in the memo for review.
        "Memo": "shopify consolidated" + (" - ERROR: " + " ".join(missing) if missing else ""),
    }
    return {"BankDepositHeaderObj": header, "BankDepositDetailArr": matched}, matched, missing


def create_shopify_deposit(amount=None, payout_id=None, date_min=None, date_max=None,
                           client=None, dry_run=True, store=DEFAULT_STORE):
    payout = get_payout(amount=amount, payout_id=payout_id, date_min=date_min, date_max=date_max, store=store)
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
    obj, matched, missing = build_deposit(payout, undep, exchange_rate=rate, store=store)
    det = obj["BankDepositDetailArr"]
    deposit_total = round(sum(float(r["Amount"]) for r in det), 2)
    payment_lines = sum(1 for r in det if r.get("LinkedFlag"))
    fx_id = _store_accounts(store, cur)["fx"]["Id"]
    fx = next((r["Amount"] for r in det if r.get("DepositFromAccntId") == fx_id), 0.0)
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
