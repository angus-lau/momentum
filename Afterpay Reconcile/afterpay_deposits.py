#!/usr/bin/env python3
"""
Create Xoro bank deposits from Afterpay settlements matched to Umpqua bank credits.

Usage:
    python3 afterpay_deposits.py <settlements.csv> <umpqua_export.csv>            # dry-run
    python3 afterpay_deposits.py <settlements.csv> <umpqua_export.csv> --create   # post

Afterpay pays out net of its merchant fee, batching by **settlement date**, and the
money lands in Umpqua 1–3 days later — so one ``AFTERPAY ... EDI PAYMNT`` credit on
the bank statement can cover several settlement dates (a refund on one date is
carried into the next payout). This matches each bank credit to the contiguous run
of settlement dates that sums to it, then books a deposit:

    payment line per order   the Xoro undeposited Customer Deposit / Refund row
    fee line                 gross - bank amount, to 7456 CC Processing Fees (USD)
                             (absorbs Afterpay's own half-up rounding, <= 1c)

The join to Xoro is two hops, because Afterpay only knows its own token:
``Merchant Order ID`` -> Shopify order search -> ``order_number`` -> Xoro ``ChequeNo``.
(The same Shopify-search trick the PayPal flow uses; the token isn't a literal field
on the order but Shopify's search indexes it.)

Split tenders are fine: an order part-paid by Afterpay has a Xoro undeposited row for
the Afterpay portion only, which is what matches the settlement's Order Amount.

Dry-run by default. Requires SHOPIFY_STORE / SHOPIFY_ADMIN_TOKEN in ../.env.
"""

import sys
import csv
import json
import pathlib
import datetime
import urllib.request
from decimal import Decimal, ROUND_HALF_UP
from collections import OrderedDict, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from xoro_webmethods import WebMethodClient, WebMethodError  # noqa: E402
from xoro_api import XoroClient  # noqa: E402

ENV_PATH = pathlib.Path(__file__).resolve().parent.parent / ".env"
SHOPIFY_API_VERSION = "2024-10"
CURRENCY, CURRENCY_ID = "USD", 1001
ADJ_STORE_ID, ADJ_STORE_NAME = 10001, "CA"

# Afterpay settles the US store into Umpqua; fee to the USD CC-processing account.
DEPOSIT_ACCOUNT = {"Id": "B7D04105A81AED1CB3EA3AB9426A", "Name": "Umpqua Bank 1729 (USD)",
                   "CurrencyId": CURRENCY_ID, "CurrencyName": CURRENCY, "GLCode": "1140"}
FEE_ACCOUNT = {"Id": "B7D1B02C7EB5CD837D800F3B405B", "Name": "Credit Card Processing Fees (USD)",
               "TypeId": 1034, "CurrencyId": CURRENCY_ID, "CurrencyName": CURRENCY}

BANK_DESC_KEY = "AFTERPAY"


class MatchError(Exception):
    """A bank credit could not be explained by any run of settlement dates."""


# ---------- money ----------

def money(s):
    s = (s or "").strip().replace("$", "").replace(",", "")
    return Decimal(s) if s else Decimal(0)


def cents(d):
    """Round to cents the way Afterpay does — half-up, not Python's banker's rounding."""
    return Decimal(d).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fee_for(gross, bank_amount):
    """The fee line that makes the deposit tie to the bank exactly.

    Afterpay states a fee per order, but nets and rounds the batch itself, so the
    stated fees can differ from (gross - paid) by a cent. The bank is the authority.
    """
    return cents(Decimal(gross) - Decimal(bank_amount))


# ---------- parsing ----------

def group_settlements(rows):
    """[(settlement_date, {net, rows})] in date order, net kept at full precision."""
    groups = OrderedDict()
    for r in rows:
        d = datetime.datetime.strptime(r["Settlement Date"].strip(), "%m/%d/%Y").date()
        g = groups.setdefault(d, {"net": Decimal(0), "rows": []})
        g["net"] += money(r["Net Settlement Amount"])
        g["rows"].append(r)
    return sorted(groups.items())


def read_settlements(path):
    with open(path, newline="") as f:
        return group_settlements([r for r in csv.DictReader(f) if (r.get("Settlement Date") or "").strip()])


def read_bank_credits(path):
    """[(post_date, credit_amount)] for the Afterpay rows of an Umpqua/Columbia export."""
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            blob = " ".join(str(v) for v in r.values() if v).upper()
            if BANK_DESC_KEY not in blob:
                continue
            credit = money(r.get(" Credit") or r.get("Credit"))
            if credit <= 0:
                continue
            d = datetime.datetime.strptime(r["Post Date"].strip(), "%m/%d/%Y").date()
            out.append((d, credit))
    return sorted(out)


# ---------- matching ----------

def match_bank_rows(bank, groups):
    """Explain every bank credit as a contiguous run of settlement dates.

    Walks both lists forward: a settlement is used at most once, only settlements
    dated on/before the credit can pay it, and settlements left behind were paid
    outside this bank window (reported as ``skipped_before``).
    """
    matches, i = [], 0
    for bdate, amount in bank:
        hit = None
        for start in range(i, len(groups)):
            if groups[start][0] > bdate:
                break
            total = Decimal(0)
            for end in range(start, len(groups)):
                if groups[end][0] > bdate:
                    break
                total += groups[end][1]["net"]
                if cents(total) == amount:
                    hit = (start, end + 1)
                    break
            if hit:
                break
        if not hit:
            raise MatchError(
                "bank credit %s on %s matches no run of settlement dates "
                "(unpaid settlements: %s)" % (amount, bdate,
                                              ", ".join(str(d) for d, _ in groups[i:]) or "none"))
        start, end = hit
        matches.append({"date": bdate, "amount": amount, "groups": groups[start:end],
                        "skipped_before": [d for d, _ in groups[i:start]]})
        i = end
    return matches


# ---------- Shopify ----------

def _env(key):
    for line in open(ENV_PATH):
        line = line.strip()
        if line.startswith(key + "="):
            return line.partition("=")[2].strip().strip('"').strip("'")
    return None


def shopify_order_numbers(tokens):
    """{Merchant Order ID -> Shopify order_number or None}, one search per token.

    Afterpay's token isn't a field on the order, but Shopify's order search indexes
    it (same behaviour the PayPal flow relies on for transaction ids).
    """
    domain, tok = _env("SHOPIFY_STORE"), _env("SHOPIFY_ADMIN_TOKEN")
    if not domain or not tok:
        raise RuntimeError("missing SHOPIFY_STORE / SHOPIFY_ADMIN_TOKEN in .env")
    out = {}
    for t in tokens:
        q = ('{ orders(first: 2, query: "%s") { edges { node { name } } } }' % t)
        req = urllib.request.Request(
            "https://%s/admin/api/%s/graphql.json" % (domain, SHOPIFY_API_VERSION),
            data=json.dumps({"query": q}).encode(),
            headers={"X-Shopify-Access-Token": tok, "Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        edges = ((data.get("data") or {}).get("orders") or {}).get("edges") or []
        out[t] = edges[0]["node"]["name"] if edges else None
    return out


# ---------- deposit ----------

def _fee_line(amount, line_number, txn_date):
    a = FEE_ACCOUNT
    return {"AllowDuplicateThirdPartyRefNo": False, "Amount": float(amount), "BankDepositId": 0,
            "ChequeNo": "", "DeleteFlag": False,
            "DepositFromAccntCurrencyId": a["CurrencyId"], "DepositFromAccntCurrencyName": a["CurrencyName"],
            "DepositFromAccntId": a["Id"], "DepositFromAccntName": a["Name"],
            "DepositFromAccntTypeId": a.get("TypeId"),
            "EntityAccountId": "", "EntityName": "", "EntityTypeId": 0, "EntityTypeName": "",
            "Id": 0, "LineNumber": line_number, "LinkedFlag": None, "LinkedTxnTableId": 0,
            "TxnDate": txn_date, "LinkedTxnDate": txn_date, "Memo": "Afterpay fees",
            "StoreId": ADJ_STORE_ID, "StoreName": ADJ_STORE_NAME}


def build_deposit(match, order_numbers, undeposited):
    """(bankDepositObj, matched_rows, missing) for one bank credit.

    One payment line per settlement row, matched to the Xoro undeposited row with the
    same ChequeNo and the same sign (a refund picks the negative row), never reusing
    a row — an order can have both a charge and a refund leg.
    """
    by_cheque = defaultdict(list)
    for r in undeposited:
        if r.get("ChequeNo") is not None:
            by_cheque[str(r["ChequeNo"]).strip()].append(r)

    txn_date = match["date"].strftime("%-m/%-d/%Y")
    lines, missing, used, gross = [], [], set(), Decimal(0)
    for _sdate, g in match["groups"]:
        for row in g["rows"]:
            token = row["Merchant Order ID"]
            order = order_numbers.get(token)
            signed = -money(row["Order Amount"]) if row["Type"].lower().startswith("refund") \
                else money(row["Order Amount"])
            if not order:
                missing.append("%s(%s %s)" % (token, row["Type"], signed))
                continue
            cands = [r for r in by_cheque.get(str(order), []) if id(r) not in used]
            same = [r for r in cands if (float(r["Amount"]) < 0) == (signed < 0)]
            pool = same or cands
            if not pool:
                missing.append("%s(%s %s)" % (order, row["Type"], signed))
                continue
            best = min(pool, key=lambda r: abs(Decimal(str(r["Amount"])) - signed))
            used.add(id(best))
            line = dict(best)
            line["LinkedFlag"] = True
            line["LineNumber"] = len(lines)
            lines.append(line)
            gross += signed

    fee = fee_for(gross, match["amount"]) if not missing else Decimal(0)
    if fee:
        lines.append(_fee_line(-fee, len(lines), txn_date))

    memo = "afterpay usd" + (" - ERROR: " + " ".join(missing) if missing else "")
    header = {
        "Id": -1, "TxnId": None, "TxnNo": -1, "TxnDate": txn_date, "BankDepositNumber": None,
        "DepositToAccntId": DEPOSIT_ACCOUNT["Id"], "DepositToAccntName": DEPOSIT_ACCOUNT["Name"],
        "DepositToAccntCurrencyId": CURRENCY_ID, "TotalAmount": 0,
        "CurrencyCode": CURRENCY, "CurrencyId": CURRENCY_ID,
        "HomeCurrencyId": 1, "HomeCurrencyName": "CAD", "ExchangeRate": "1",
        "CashBackMemo": "", "CashBackAccntId": "", "CashBackAccntCurrencyId": "",
        "CashBackAccntName": "", "CashBackAmount": 0, "Memo": memo,
    }
    return {"BankDepositHeaderObj": header, "BankDepositDetailArr": lines}, lines, missing


def check_duplicate(match):
    """Raise if a Bank Deposit of this amount already sits on Umpqua near this date."""
    d = match["date"]
    start = (d - datetime.timedelta(days=3)).isoformat()
    end = (d + datetime.timedelta(days=3)).isoformat()
    x = XoroClient.from_config() if hasattr(XoroClient, "from_config") else XoroClient()
    rows = x.get_gl_transactions(start, end, account_gl_codes=DEPOSIT_ACCOUNT["GLCode"])
    hit = next((r for r in rows if r.get("TxnTypeName") == "Bank Deposit"
                and cents(Decimal(str(r.get("Amount") or 0))) == match["amount"]), None)
    if hit:
        raise WebMethodError(
            "a Bank Deposit (%s, %s) already exists near %s — refusing to duplicate"
            % (hit.get("TxnNumber"), match["amount"], d))


def main(settlements_csv, bank_csv, create=False):
    groups = read_settlements(settlements_csv)
    bank = read_bank_credits(bank_csv)
    print("%d settlement date(s), %d Afterpay bank credit(s)" % (len(groups), len(bank)))
    matches = match_bank_rows(bank, groups)

    tokens = sorted({r["Merchant Order ID"] for m in matches for _d, g in m["groups"] for r in g["rows"]})
    order_numbers = shopify_order_numbers(tokens)
    client = WebMethodClient.from_config()
    undeposited = client.get_undeposited_transactions(CURRENCY_ID)
    print("%d token(s) resolved, %d undeposited %s row(s) in Xoro\n"
          % (sum(1 for v in order_numbers.values() if v), len(undeposited), CURRENCY))

    for m in matches:
        obj, lines, missing = build_deposit(m, order_numbers, undeposited)
        total = cents(sum(Decimal(str(l["Amount"])) for l in lines))
        ok = total == m["amount"] and not missing
        print("BANK %s  %s   <- settle %s" % (m["date"], m["amount"],
                                              ", ".join(str(d) for d, _ in m["groups"])))
        for l in lines:
            tag = l.get("ChequeNo") or l.get("Memo") or ""
            print("    %10s  %s" % (l["Amount"], tag))
        print("    total %s  %s" % (total, "OK" if ok else "MISMATCH %s" % (missing or "")))
        if m["skipped_before"]:
            print("    (settled %s — paid outside this bank window)"
                  % ", ".join(str(d) for d in m["skipped_before"]))
        if not create:
            print()
            continue
        if not ok:
            print("    not created (does not balance)\n")
            continue
        check_duplicate(m)
        client.create_bank_deposit(obj)
        print("    created\n")

    unpaid = [d for d, _ in groups if all(d not in [x for x, _ in m["groups"]] for m in matches)]
    if unpaid:
        print("settlements not yet deposited in this bank window: %s"
              % ", ".join(str(d) for d in unpaid))
    if not create:
        print("Dry run — pass --create to post these deposits")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        sys.exit(1)
    main(args[0], args[1], create="--create" in sys.argv)
