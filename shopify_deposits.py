"""Create Xoro bank deposits from Shopify payouts (gap reconcile).

For a Shopify payout, matches its order numbers to Xoro undeposited payments
(by the undeposited row's ``ChequeNo`` == the Shopify order number), builds a
bank deposit containing those payments plus a Shopify-fee cash-back line, and
lists any unmatched orders in the deposit's memo.

A payout is often built before every order has synced from Shopify into
Xoro's Undeposited Funds — some show up hours or days later. ``retry_open_deposits``
re-checks every short deposit (memo has ``- ERROR:``) and tops it up in place
via ``updateBankDeposit`` as those orders arrive, instead of leaving it short
forever. ``create_shopify_deposit`` also refuses to create a duplicate if a
Bank Deposit for the same amount already exists near the payout date (someone
may have already booked it manually) — pass ``check_duplicate=False`` to skip.

Dry-run by default — prints the exact deposit without creating it.

    python3 shopify_deposits.py                       # dry-run the most recent payout
    python3 shopify_deposits.py 5898.38                # dry-run a specific payout by amount
    python3 shopify_deposits.py --retry                # top up every short deposit (default store)
    python3 shopify_deposits.py --retry service_center # same, other store
"""

import json
import os
import re
import urllib.request
from datetime import datetime, timedelta

from xoro_api import XoroClient
from xoro_webmethods import WebMethodClient, WebMethodError

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
                            "CurrencyId": 1001, "CurrencyName": "USD", "GLCode": "1140"},
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
                            "CurrencyId": 1, "CurrencyName": "CAD", "GLCode": "1160"},
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
        raw_id = t.get("source_order_id")
        onm = onum.get(str(raw_id)) if raw_id else None
        if typ in ("charge", "refund"):
            orders.append({"order": onm, "amount": amt, "type": typ, "raw_order_id": raw_id})
        else:                        # debit / credit / adjustment / dispute ... -> adjustment
            adjustments.append({"amount": amt, "type": typ, "order": onm, "raw_order_id": raw_id})
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
        if o["order"] is None:                              # order deleted from Shopify -> no order #
            ref = "shopify_id:%s" % o["raw_order_id"] if o.get("raw_order_id") else "no-shopify-id"
            missing.append("%s(%s %.2f)" % (ref, o["type"], o["amount"]))
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
        # The originating Shopify payout id -- lets retry_bank_deposit find its way
        # back to the payout later without the caller needing to remember it.
        "ThirdPartyRefNo": str(payout["id"]),
        # Anything unmatched (deleted/no-order charges, unfound orders, big adjustments)
        # means the deposit is short of the payout -> flag ERROR in the memo for review.
        # Comma-joined (not space) so retry_bank_deposit can parse it back out reliably --
        # individual entries like "shopify_id:X(refund -1.23)" contain internal spaces.
        "Memo": "shopify consolidated" + (" - ERROR: " + ",".join(missing) if missing else ""),
    }
    return {"BankDepositHeaderObj": header, "BankDepositDetailArr": matched}, matched, missing


def _deposit_gl_code(store, currency):
    return _store_accounts(store, currency)["deposit"].get("GLCode")


def _check_duplicate(payout, store):
    """Raise if a Bank Deposit for this exact payout amount already exists within
    a few days of the payout date -- catches a payout someone already booked
    manually (or via an earlier run) before we'd double it up. A payout's actual
    posting date can drift a day or two from its Shopify date, hence the window.
    """
    gl_code = _deposit_gl_code(store, payout["currency"])
    if not gl_code:
        return  # no GL code configured for this account -- skip rather than block
    pdate = datetime.strptime(payout["date"], "%Y-%m-%d")
    start = (pdate - timedelta(days=3)).strftime("%Y-%m-%d")
    end = (pdate + timedelta(days=3)).strftime("%Y-%m-%d")
    rows = XoroClient().get_gl_transactions(start, end, account_gl_codes=gl_code)
    amt = round(payout["amount"], 2)
    hit = next((r for r in rows if r.get("TxnTypeName") == "Bank Deposit"
                and round(float(r.get("Amount", 0)), 2) == amt), None)
    if hit:
        raise WebMethodError(
            "a Bank Deposit (%s, %.2f) already exists near %s for this payout -- "
            "refusing to create a duplicate. Pass check_duplicate=False to override."
            % (hit.get("RefNumber"), amt, payout["date"])
        )


def create_shopify_deposit(amount=None, payout_id=None, date_min=None, date_max=None,
                           client=None, dry_run=True, store=DEFAULT_STORE, check_duplicate=True):
    payout = get_payout(amount=amount, payout_id=payout_id, date_min=date_min, date_max=date_max, store=store)
    if not dry_run and check_duplicate:
        _check_duplicate(payout, store)
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


def find_open_deposits(store, days=45, client=None):
    """Scan ``store``'s deposit account for Bank Deposits still short a payout
    (memo contains ``- ERROR:``) within the last ``days`` days.

    Returns a list of ``{bank_deposit_id, bd_number, amount, date, memo}`` --
    feed each ``bank_deposit_id`` to ``retry_bank_deposit``.
    """
    cur = next(iter(STORES[store]["accounts"]))
    gl_code = _deposit_gl_code(store, cur)
    if not gl_code:
        raise KeyError("no GLCode configured for store %r's deposit account" % store)
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    rows = XoroClient().get_gl_transactions(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
                                            account_gl_codes=gl_code)
    return [
        {"bank_deposit_id": r.get("RefId"), "bd_number": r.get("RefNumber"),
         "amount": r.get("Amount"), "date": r.get("TxnDate"), "memo": r.get("Memo")}
        for r in rows
        if r.get("TxnTypeName") == "Bank Deposit" and "- ERROR:" in (r.get("Memo") or "")
    ]


def retry_bank_deposit(bank_deposit_id, store, client=None):
    """Re-check a short deposit's missing refs against Undeposited Funds and add
    any that have since synced in, via ``updateBankDeposit``. No-ops (no API call)
    if nothing new is found. Only bare order numbers are retriable -- deleted-order
    refunds and large adjustments noted in the memo can never resolve, so they're
    left as still_missing untouched.

    Returns ``{bank_deposit_id, added, still_missing, updated, new_total?}``.
    """
    client = client or WebMethodClient.from_config()
    data = client.get_bank_deposit(bank_deposit_id)
    header = data["BankDepositHeaderObj"]
    lines = data["BankDepositDetailArr"]

    _, _, err = (header.get("Memo") or "").partition("- ERROR:")
    all_refs = [x.strip() for x in err.split(",") if x.strip()]
    retriable = [x for x in all_refs if re.match(r"^\d+$", x)]
    unretriable = [x for x in all_refs if x not in retriable]

    payout_id = header.get("ThirdPartyRefNo")
    expected = {}
    if payout_id and retriable:
        payout = get_payout(payout_id=payout_id, store=store)
        for o in payout["orders"]:
            if o["order"]:
                expected.setdefault(str(o["order"]), []).append(o["amount"])

    undep = client.get_undeposited_transactions(CURRENCY_ID[header["CurrencyCode"]])
    by_cheque = {}
    for r in undep:
        cn = r.get("ChequeNo")
        if cn:
            by_cheque.setdefault(str(cn), []).append(r)

    added, still_missing = [], list(unretriable)
    for ref in retriable:
        cands = by_cheque.get(ref, [])
        if not cands:
            still_missing.append(ref)
            continue
        want = expected.get(ref, [None])[0]
        best = min(cands, key=lambda r: abs(float(r["Amount"]) - want)) if want is not None else cands[0]
        row = dict(best)
        row["LinkedFlag"] = True
        added.append((ref, row))

    if not added:
        return {"bank_deposit_id": bank_deposit_id, "added": [], "still_missing": still_missing, "updated": False}

    for i, (ref, row) in enumerate(added, start=len(lines) + 1):
        row["LineNumber"] = i
        lines.append(row)

    header["Memo"] = "shopify consolidated" + (" - ERROR: " + ",".join(still_missing) if still_missing else "")
    header["TotalAmount"] = round(sum(l["Amount"] for l in lines), 2)

    client.update_bank_deposit({"BankDepositHeaderObj": header, "BankDepositDetailArr": lines})
    return {"bank_deposit_id": bank_deposit_id, "added": [r for r, _ in added],
            "still_missing": still_missing, "updated": True, "new_total": header["TotalAmount"]}


def retry_open_deposits(store, client=None):
    """Sweep every open (short) deposit for ``store`` and top up what's newly available."""
    client = client or WebMethodClient.from_config()
    results = []
    for o in find_open_deposits(store, client=client):
        r = retry_bank_deposit(o["bank_deposit_id"], store, client=client)
        r["bd_number"] = o["bd_number"]
        results.append(r)
    return results


if __name__ == "__main__":
    import sys
    argv = sys.argv[1:]

    if argv and argv[0] == "--retry":
        store = argv[1] if len(argv) > 1 else DEFAULT_STORE
        print("=== RETRY: topping up open deposits for %s ===" % STORES[store]["label"])
        results = retry_open_deposits(store)
        if not results:
            print("no open (short) deposits found")
        for r in results:
            status = ("added %s -> now %.2f" % (r["added"], r["new_total"])) if r["updated"] else "nothing new"
            print("%s: %s%s" % (r["bd_number"], status,
                                 (" | still missing: %s" % r["still_missing"]) if r["still_missing"] else ""))
        raise SystemExit(0)

    amt = float(argv[0]) if len(argv) > 0 and argv[0] else None
    date_min = argv[1] if len(argv) > 1 else None
    date_max = argv[2] if len(argv) > 2 else None
    r = create_shopify_deposit(amount=amt, date_min=date_min, date_max=date_max, dry_run=True)
    s = r["summary"]
    p = s["payout"]
    print("=== DRY RUN: Shopify payout -> Xoro bank deposit ===")
    print("payout %s  %s  %.2f %s   Shopify fees %.2f" % (p["id"], p["date"], p["amount"], p["currency"], p["fees"]))
    print("payment lines: %d    missing (-> memo): %s" % (s["payment_lines"], s["missing_orders"] or "none"))
    print("fee line:      %.2f  -> 7456 USD / 7455 CAD" % s["fee"])
    print("FX line:       %+.2f  -> 8151 USD / 8150 CAD" % s["fx_residual"])
    print("DEPOSIT TOTAL: %.2f    payout: %.2f    BALANCES: %s" % (s["deposit_total"], p["amount"], s["balances"]))
    print("deposit to:    %s   exchange rate: %s" % (s["deposit_to"], s["exchange_rate"]))
    print("memo:          %s" % (s["memo"] or "(empty - nothing missing)"))
