"""Create Xoro bank deposits from Stripe payouts (gap reconcile).

For a Stripe payout, matches each charge/refund to a Xoro undeposited payment
by the undeposited row's ``LineRefNo`` == the Stripe charge's ``description``
(e.g. ``CA-CD037767`` — a Xoro Customer Deposit reference). This is the same
API (``BankDepositWebMethods.createBankDeposit``) ``shopify_deposits.py``
uses; only the match key differs (``LineRefNo`` here vs. ``ChequeNo`` for
Shopify order numbers).

Verified live 2026-08-22: three real August payout refs (CA-CD037767,
CA-CD037662, CA-CD037461) matched exactly against undeposited CAD rows'
``LineRefNo``, amounts equal to the gross charge amount, ``TxnTypeName``
"Customer Deposit".

ACCOUNTS below is STUBBED — the deposit-to bank account and fee/FX GL
accounts are placeholders. Dry-run works today (shows the shape of what
would be created); a live (non-dry-run) create raises until the real
FAccountingIds are filled in. Find them via
``xoro_webmethods.WebMethodClient.get_bank_statement_accounts()`` for the
bank account, and ``AccountingWebMethods.getAllAccountsForApi`` (or the GL
rows) for the fee/FX GL codes — see ``XORO_API.md``.

Dry-run by default — prints the exact deposit without creating it.

    python3 stripe_deposits.py                          # most recent payout
    python3 stripe_deposits.py 335.74                   # payout by amount
    python3 stripe_deposits.py "" 2026-08-01 2026-08-22  # payout by date range
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from xoro_webmethods import WebMethodClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")

CURRENCY_ID = {"CAD": 1, "USD": 1001}
ADJ_STORE_ID = 10001

# ---- Deposit/fee/FX accounts — STUBBED, fill in before a live create ------
# Shape matches shopify_deposits.py's STORES[...]["accounts"][currency]: each
# entry needs {Id, Name, CurrencyId, CurrencyName} (fee/fx also want TypeId,
# e.g. 1034 for a CC-processing-fee account, 1016 for Exchange Rate Gain/Loss)
# so the deposit line renders with a *named* account in Xoro (an id-only line
# renders blank).
ACCOUNTS = {
    "CAD": {
        "deposit": {"Id": "STUB_DEPOSIT_ACCOUNT_ID", "Name": "STUB — Stripe CAD deposit-to bank account",
                    "CurrencyId": 1, "CurrencyName": "CAD"},
        "fee":     {"Id": "STUB_FEE_ACCOUNT_ID", "Name": "STUB — Stripe CAD processing fees",
                    "TypeId": 1034, "CurrencyId": 1, "CurrencyName": "CAD"},
        "fx":      {"Id": "STUB_FX_ACCOUNT_ID", "Name": "STUB — Exchange Rate Gain/Loss (CAD)",
                    "TypeId": 1016, "CurrencyId": 1, "CurrencyName": "CAD"},
    },
}


def _accounts(currency):
    accts = ACCOUNTS.get(currency)
    if not accts:
        raise KeyError("no Stripe account config for currency %r — add it to ACCOUNTS" % currency)
    return accts


def _is_stub(accts):
    return any(str(v.get("Id", "")).startswith("STUB_") for v in accts.values())


def _adjustment_line(acct, amount, memo, line_number, txn_date):
    """A non-payment deposit line (fee or FX) drawn from GL account ``acct``."""
    return {
        "AllowDuplicateThirdPartyRefNo": False, "Amount": round(amount, 2), "BankDepositId": 0,
        "ChequeNo": "", "DeleteFlag": False,
        "DepositFromAccntCurrencyId": acct["CurrencyId"], "DepositFromAccntCurrencyName": acct["CurrencyName"],
        "DepositFromAccntId": acct["Id"], "DepositFromAccntName": acct["Name"],
        "DepositFromAccntTypeId": acct.get("TypeId"),
        "EntityAccountId": "", "EntityName": "", "EntityTypeId": 0, "EntityTypeName": "",
        "Id": 0, "LineNumber": line_number,
        "LinkedFlag": None, "LinkedTxnTableId": 0,
        "TxnDate": txn_date, "LinkedTxnDate": txn_date,
        "Memo": memo, "StoreId": ADJ_STORE_ID, "StoreName": "CA",
    }


def _env(key, env_path=ENV_PATH):
    if not os.path.exists(env_path):
        return None
    for line in open(env_path):
        line = line.strip()
        if line.startswith(key + "="):
            return line.partition("=")[2].strip().strip('"').strip("'")
    return None


def _stripe(path, params=None):
    key = _env("STRIPE_LIVE_KEY")
    if not key:
        raise RuntimeError("missing STRIPE_LIVE_KEY in .env")
    qs = urllib.parse.urlencode(params or {}, doseq=True)
    url = "https://api.stripe.com/v1%s%s" % (path, ("?" + qs if qs else ""))
    req = urllib.request.Request(url, headers={"Authorization": "Bearer %s" % key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError("Stripe API error %s: %s" % (e.code, e.read().decode()[:300]))


def _to_ts(d):
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _iso_to_slash(d):
    y, m, dd = d[:10].split("-")
    return "%d/%d/%d" % (int(m), int(dd), int(y))


def get_payout(amount=None, payout_id=None, date_min=None, date_max=None):
    """Return a payout dict: id, date, amount, currency, fees, items, adjustments.

    ``items`` are charge/refund transactions with a Xoro-matchable ``ref``
    (the balance-transaction ``description``, e.g. ``CA-CD037767``).
    ``adjustments`` are everything else (Stripe fees, disputes, topups, ...).
    """
    if payout_id:
        p = _stripe("/payouts/%s" % payout_id)
    else:
        params = {"limit": 100}
        if date_min:
            params["arrival_date[gte]"] = _to_ts(date_min)
        if date_max:
            params["arrival_date[lte]"] = _to_ts(date_max)
        payouts = _stripe("/payouts", params)["data"]
        if amount is not None:
            p = next((x for x in payouts if abs(x["amount"] / 100 - amount) < 0.005), None)
        else:
            p = payouts[0] if payouts else None
        if not p:
            raise ValueError("payout not found")

    txns = []
    starting_after = None
    while True:
        params = {"payout": p["id"], "limit": 100}
        if starting_after:
            params["starting_after"] = starting_after
        bt = _stripe("/balance_transactions", params)
        txns.extend(bt["data"])
        if not bt.get("has_more"):
            break
        starting_after = bt["data"][-1]["id"]

    items, adjustments, fees = [], [], 0.0
    for t in txns:
        typ = t["type"]
        if typ == "payout":              # the payout line itself, not a deposit item
            continue
        amt, fee = t["amount"] / 100.0, t["fee"] / 100.0
        fees += fee
        ref = (t.get("description") or "").strip()
        if typ in ("charge", "refund", "payment", "payment_refund") and ref:
            items.append({"ref": ref, "amount": amt, "type": typ})
        else:
            adjustments.append({"amount": amt, "type": typ, "ref": ref})

    return {
        "id": p["id"],
        "date": datetime.fromtimestamp(p["arrival_date"], tz=timezone.utc).strftime("%Y-%m-%d"),
        "amount": p["amount"] / 100.0, "currency": p["currency"].upper(),
        "fees": round(fees, 2), "items": items, "adjustments": adjustments,
    }


def build_deposit(payout, undeposited_rows, exchange_rate="1"):
    """Return (bankDepositObj, matched_rows, missing_refs).

    Matches each payout item to an undeposited row by ``LineRefNo``. An
    item's amount sign picks between a deposit row and a refund row when a
    ref has both (mirrors ``shopify_deposits.build_deposit``'s ChequeNo
    matching, just keyed on ``LineRefNo`` instead).
    """
    by_ref = defaultdict(list)
    for r in undeposited_rows:
        ref = r.get("LineRefNo")
        if ref:
            by_ref[str(ref)].append(r)

    matched, missing = [], []
    used = set()
    matched_stripe = 0.0
    for it in payout["items"]:
        want_neg = it["amount"] < 0
        cands = [r for r in by_ref.get(it["ref"], []) if id(r) not in used]
        same = [r for r in cands if (float(r["Amount"]) < 0) == want_neg]
        pool = same or cands
        if pool:
            best = min(pool, key=lambda r: abs(float(r["Amount"]) - it["amount"]))
            used.add(id(best))
            r = dict(best)
            r["LinkedFlag"] = True
            r["LineNumber"] = len(matched)
            matched.append(r)
            matched_stripe += it["amount"]
        else:
            missing.append("%s(%.2f)" % (it["ref"] or "no-ref", it["amount"]))

    cur = payout["currency"]
    cid = CURRENCY_ID.get(cur)
    if cid is None:
        raise KeyError("no Xoro currencyId mapping for %r — add it to CURRENCY_ID" % cur)
    accts = _accounts(cur)
    pdate = _iso_to_slash(payout["date"])

    # Small adjustments (Stripe fees/disputes/etc., |amount| < 50) book to the
    # fee GL; larger ones surface in the memo for manual handling.
    for a in payout.get("adjustments", []):
        if abs(a["amount"]) < 50:
            matched.append(_adjustment_line(accts["fee"], a["amount"], "Stripe adjustment (%s)" % a["type"],
                                            len(matched), pdate))
        else:
            missing.append("%s-adj(%.2f)" % (a["type"], a["amount"]))

    matched.append(_adjustment_line(accts["fee"], -payout["fees"], "Stripe fees", len(matched), pdate))

    matched_xoro = round(sum(float(r["Amount"]) for r in matched if r.get("LinkedFlag")), 2)
    fx = round(matched_stripe - matched_xoro, 2)
    if abs(fx) >= 0.01:
        matched.append(_adjustment_line(accts["fx"], fx, "FX/rounding", len(matched), pdate))

    bank = accts["deposit"]
    header = {
        "Id": -1, "TxnId": None, "TxnNo": -1, "TxnDate": pdate,
        "BankDepositNumber": None,
        "DepositToAccntId": bank["Id"], "DepositToAccntName": bank["Name"],
        "DepositToAccntCurrencyId": cid,
        "TotalAmount": 0, "CurrencyCode": cur, "CurrencyId": cid,
        "HomeCurrencyId": 1, "HomeCurrencyName": "CAD", "ExchangeRate": str(exchange_rate),
        "CashBackMemo": "", "CashBackAccntId": "", "CashBackAccntCurrencyId": "",
        "CashBackAccntName": "", "CashBackAmount": 0,
        "Memo": "stripe consolidated" + (" - ERROR: " + " ".join(missing) if missing else ""),
    }
    return {"BankDepositHeaderObj": header, "BankDepositDetailArr": matched}, matched, missing


def create_stripe_deposit(amount=None, payout_id=None, date_min=None, date_max=None,
                          client=None, dry_run=True):
    payout = get_payout(amount=amount, payout_id=payout_id, date_min=date_min, date_max=date_max)
    client = client or WebMethodClient.from_config()
    cur = payout["currency"]
    accts = _accounts(cur)
    if not dry_run and _is_stub(accts):
        raise RuntimeError(
            "ACCOUNTS[%r] still has STUB_ account ids — fill in the real deposit/fee/fx "
            "FAccountingIds before creating a live deposit (dry_run=True works today)." % cur
        )

    undep = client.get_undeposited_transactions(CURRENCY_ID[cur])
    rate = "1"
    if cur != "CAD":
        try:
            hc = (client.get_data_for_bank_deposit() or {}).get("HomeCurrencyObj") or {}
            rate = str(hc.get("ExchangeRate") or hc.get("Rate") or rate)
        except Exception:  # noqa: BLE001
            pass

    obj, matched, missing = build_deposit(payout, undep, exchange_rate=rate)
    det = obj["BankDepositDetailArr"]
    deposit_total = round(sum(float(r["Amount"]) for r in det), 2)
    payment_lines = sum(1 for r in det if r.get("LinkedFlag"))
    fx_id = accts["fx"]["Id"]
    fx = next((r["Amount"] for r in det if r.get("DepositFromAccntId") == fx_id), 0.0)
    summary = {
        "payout": {k: payout[k] for k in ("id", "date", "amount", "currency", "fees")},
        "undeposited_pulled": len(undep),
        "payment_lines": payment_lines, "missing_refs": missing,
        "fee": round(-payout["fees"], 2), "fx_residual": fx,
        "deposit_total": deposit_total,
        "balances": abs(deposit_total - payout["amount"]) < 0.01,
        "deposit_to": obj["BankDepositHeaderObj"]["DepositToAccntId"],
        "stub_accounts": _is_stub(accts),
        "memo": obj["BankDepositHeaderObj"]["Memo"],
    }
    if dry_run:
        return {"dry_run": True, "summary": summary, "payload": obj}
    return {"created": True, "summary": summary, "deposit": client.create_bank_deposit(obj)}


if __name__ == "__main__":
    import sys
    argv = sys.argv[1:]
    amt = float(argv[0]) if len(argv) > 0 and argv[0] else None
    date_min = argv[1] if len(argv) > 1 else None
    date_max = argv[2] if len(argv) > 2 else None

    r = create_stripe_deposit(amount=amt, date_min=date_min, date_max=date_max, dry_run=True)
    s = r["summary"]
    p = s["payout"]
    print("=== DRY RUN: Stripe payout -> Xoro bank deposit ===")
    if s["stub_accounts"]:
        print("!!! ACCOUNTS still has STUB_ ids — fill in the real deposit/fee/fx accounts before going live !!!")
    print("payout %s  %s  %.2f %s   Stripe fees %.2f" % (p["id"], p["date"], p["amount"], p["currency"], p["fees"]))
    print("payment lines: %d    missing (-> memo): %s" % (s["payment_lines"], s["missing_refs"] or "none"))
    print("fee line:      %.2f" % s["fee"])
    print("FX/rounding:   %+.2f" % s["fx_residual"])
    print("DEPOSIT TOTAL: %.2f    payout: %.2f    BALANCES: %s" % (s["deposit_total"], p["amount"], s["balances"]))
    print("deposit to:    %s" % s["deposit_to"])
    print("memo:          %s" % (s["memo"] or "(empty - nothing missing)"))
