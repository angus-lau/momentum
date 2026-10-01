#!/usr/bin/env python3
"""
Build the month's POS cash bank deposit for `1121 - Cash - Service Centre`.

The POS cash sales already exist in Xoro as undeposited **Cash** Invoice Payments
(and Customer Deposits / credit refunds) — they are not created here. This script
matches them against the month's Shopify POS register session and deposits them,
which is what `BD052126` was for July.

Matching uses TWO keys, so a coincidental amount can't pull in the wrong payment:
the POS receipt number (Shopify names the order ``C36335``, Xoro stores the
receipt bare in ``ChequeNo``/``LineRefNo2`` as ``36335``) AND the amount.

Anything the session shows that Xoro hasn't got is named in the deposit memo as
``MISSING:``, following the Shopify/PayPal convention, so a short deposit is
self-documenting and can be topped up later with ``updateBankDeposit``.

Cash rounding: Shopify's ``totalCashSales`` can exceed the sum of its own cash
transactions, because POS cash rounds to the nickel (no Canadian penny). That
difference is NOT put on the deposit — the deposit totals the actual payments.
It goes on the bank statement import as a penny-rounding line, the way July's
0.01 went to `6601 - Penny Rounding Adjustments - CAD` via `JE016761`.

Usage:
    python3 pos_cash_deposits.py 2026-08 --dry-run
    python3 pos_cash_deposits.py 2026-08
    python3 pos_cash_deposits.py 2026-08 --open      # also open it in the browser
"""

import re
import sys
import webbrowser
from datetime import date
from decimal import Decimal

import shopify_pos_cash as pos
from xoro_webmethods import WebMethodClient

CASH_ACCOUNT = {"Id": "B7D04105A81A0E8E01A93DB24565",
                "Name": "1121 - Cash - Service Centre",
                "CurrencyId": 1, "CurrencyCode": "CAD", "GLCode": "1121"}
CAD = 1
CASH_METHOD = "Cash"
# Xoro's UI is ASP.NET WebForms behind a client-side router; `/Accounting/BankDeposit/
# BankDeposit.aspx` is the real page (every other spelling returns Xoro's error page),
# but the per-deposit deep-link parameter is not known, so this opens the deposit
# screen and the month's deposit is identified by its number in the output.
DEPOSIT_PAGE = "https://momentum.xoro.one/Accounting/BankDeposit/BankDeposit.aspx"

# Shopify names a POS order "C<receipt>"; Xoro keeps the receipt bare.
RECEIPT = re.compile(r"^C?0*(\d+)$")


class NotFound(RuntimeError):
    pass


def receipt_of(order_name):
    m = RECEIPT.match((order_name or "").strip())
    return m.group(1) if m else None


def _last_day(y, m):
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def session_cash(month):
    """The month's POS cash movements, as ``{receipt: (amount, order_name)}``.

    A session's ``cashTransactions`` covers sales and refunds; a refund is carried
    as a negative so the deposit nets exactly like July's did.
    """
    sessions = pos.month_sessions(month)
    closed = [s for s in sessions if s.get("closingTime")]
    if not closed:
        raise NotFound("no closed POS register session for %s" % month)
    out, reported = {}, Decimal("0")
    for s in closed:
        for e in ((s.get("cashTransactions") or {}).get("edges") or []):
            n = e["node"]
            if (n.get("status") or "SUCCESS").upper() not in ("SUCCESS", ""):
                continue
            amount = Decimal(((n.get("amountSet") or {}).get("presentmentMoney") or {})["amount"])
            if (n.get("kind") or "").upper() in ("REFUND", "VOID"):
                amount = -amount
            name = (n.get("order") or {}).get("name")
            r = receipt_of(name)
            if r is None:
                continue
            out.setdefault(r, []).append((amount, name))
        reported += (pos._money(s.get("netCashSales")) or Decimal(0))
    return out, reported, closed


def undeposited_cash(client):
    """Undeposited CAD rows taken in cash, keyed by receipt number."""
    rows = client.get_undeposited_transactions(CAD)
    by_receipt = {}
    for r in rows:
        if (r.get("PaymentMethodName") or "") != CASH_METHOD:
            continue
        key = receipt_of(str(r.get("ChequeNo") or "")) or receipt_of(str(r.get("LineRefNo2") or ""))
        if key:
            by_receipt.setdefault(key, []).append(r)
    return by_receipt


def match(session_cash_map, undeposited):
    """Pair each session cash movement with its undeposited Xoro payment.

    Both the receipt number and the amount must agree; a receipt whose amount
    disagrees is reported rather than silently deposited.
    """
    lines, missing, mismatched = [], [], []
    for receipt in sorted(session_cash_map, key=int):
        for amount, name in session_cash_map[receipt]:
            pool = undeposited.get(receipt) or []
            hit = next((r for r in pool
                        if abs(Decimal(str(r["Amount"])) - amount) < Decimal("0.005")
                        and not any(r is used for used in lines)), None)
            if hit is None:
                if pool:
                    mismatched.append("%s %s in Xoro as %s" % (
                        name, amount, ", ".join("%.2f" % r["Amount"] for r in pool)))
                else:
                    missing.append("%s %.2f" % (name, amount))
                continue
            lines.append(hit)
    return lines, missing, mismatched


def build_deposit(month, lines, missing):
    txn_date = _last_day(*(int(x) for x in month.split("-"))).strftime("%-m/%-d/%Y")
    detail = []
    for row in lines:
        line = dict(row)
        line["LinkedFlag"] = True
        line["LineNumber"] = len(detail)
        line["BankDepositId"] = 0
        detail.append(line)
    total = sum(Decimal(str(l["Amount"])) for l in detail)
    memo = "POS cash %s" % month
    if missing:
        memo += " - MISSING: " + "; ".join(missing)
    header = {
        "Id": -1, "TxnId": None, "TxnNo": -1, "TxnDate": txn_date, "BankDepositNumber": None,
        "DepositToAccntId": CASH_ACCOUNT["Id"], "DepositToAccntName": CASH_ACCOUNT["Name"],
        "DepositToAccntCurrencyId": CASH_ACCOUNT["CurrencyId"],
        "TotalAmount": float(total),
        "CurrencyCode": "CAD", "CurrencyId": CAD,
        "HomeCurrencyId": 1, "HomeCurrencyName": "CAD",
        # CAD into a CAD account with a CAD home currency: rate is 1, but it must be
        # SENT -- omitting it stores 0 and zeroes every home-currency amount.
        "ExchangeRate": "1",
        "CashBackMemo": "", "CashBackAccntId": "", "CashBackAccntCurrencyId": "",
        "CashBackAccntName": "", "CashBackAmount": 0,
        "Memo": memo,
    }
    return {"BankDepositHeaderObj": header, "BankDepositDetailArr": detail}, total


def run(month, dry_run=False, open_browser=False):
    cash_map, reported, sessions = session_cash(month)
    client = WebMethodClient.from_config() if hasattr(WebMethodClient, "from_config") else WebMethodClient()
    undeposited = undeposited_cash(client)
    lines, missing, mismatched = match(cash_map, undeposited)

    counted = sum(Decimal(str(l["Amount"])) for l in lines)
    print("session(s): %s" % ", ".join(s["id"].rsplit("/", 1)[-1] for s in sessions))
    print("POS cash movements in the session : %d" % sum(len(v) for v in cash_map.values()))
    print("matched to undeposited payments   : %d  totalling %.2f" % (len(lines), counted))
    if mismatched:
        print("AMOUNT MISMATCH (not deposited)   : %s" % "; ".join(mismatched))
    if missing:
        print("NOT IN XORO (named in the memo)   : %s" % "; ".join(missing))
    rounding = reported - sum(a for v in cash_map.values() for a, _ in v)
    if rounding:
        print("cash rounding (statement line, not on the deposit): %.2f" % rounding)

    if not lines:
        raise SystemExit("nothing to deposit for %s" % month)

    obj, total = build_deposit(month, lines, missing)
    print("\ndeposit %s into %s, %d line(s), total %.2f"
          % (obj["BankDepositHeaderObj"]["TxnDate"], CASH_ACCOUNT["Name"], len(lines), total))
    print("memo: %s" % obj["BankDepositHeaderObj"]["Memo"])
    if dry_run:
        print("\n(dry run — nothing posted)")
        return None
    created = client.create_bank_deposit(obj)
    number = created.get("BankDepositNumber") or created.get("Id")
    print("\ncreated %s (Id %s)" % (number, created.get("Id")))
    url = DEPOSIT_PAGE
    print("%s  -> find %s (dated %s)"
          % (url, number, obj["BankDepositHeaderObj"]["TxnDate"]))
    if open_browser:
        webbrowser.open(url)
    return created


if __name__ == "__main__":
    argv = sys.argv[1:]
    flags = {a for a in argv if a.startswith("--")}
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)
    run(args[0], dry_run="--dry-run" in flags, open_browser="--open" in flags)
