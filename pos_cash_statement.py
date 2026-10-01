#!/usr/bin/env python3
"""
Build and upload the month's bank statement for `1121 - Cash - Service Centre`,
then open the reconciliation.

There is no real bank statement for a till, so it is derived from the Shopify POS
register session — the physical count is the statement:

    beginning balance  = the session's COUNTED opening balance
    + POS cash sales   = the bank deposit (one line, matching the deposit total)
    + cash rounding    = Shopify's totalCashSales less the sum of its own cash
                         transactions (POS cash rounds to the nickel; no penny)
    - each adjustment  = cash taken out of / put into the till, on its own date,
                         carrying the staff note as the description
    + variance         = counted close less expected close
    = ending balance   = the session's COUNTED closing balance

Before anything is sent: the lines are asserted to carry the beginning balance to
the ending balance, and the beginning balance is cross-checked against the figure
Xoro carried forward from the prior reconciliation. Note the span assertion cannot
catch a wrong closing balance — the variance line is counted-less-expected, so it
absorbs whatever the closing count says; what it does catch is an adjustment list
that came back short of ``totalAdjustments``, which would silently lose a line.

Only the statement and the reconciliation *header* are automated. Matching the
statement lines to transactions, and coding the adjustments/variance/rounding to
their GL accounts, stays manual — as with every other account here.

Usage:
    python3 pos_cash_statement.py 2026-08 --dry-run
    python3 pos_cash_statement.py 2026-08                # upload + open the rec
    python3 pos_cash_statement.py 2026-08 --no-rec       # upload only
"""

import csv
import os
import sys
import webbrowser
from datetime import date
from decimal import Decimal

import shopify_pos_cash as pos
from pos_cash_deposits import CASH_ACCOUNT, _last_day, receipt_of
from xoro_webmethods import BANK_RECONCILE_SERVICE, WebMethodClient

BASE = ("/Users/angus/Library/CloudStorage/OneDrive-St.MoritzWatch/"
        "Accounting Docs/Bank Reconciliations")
FOLDER = "Service Centre Cash"
CSV_HEADER = ["**Date", "**Amount", "Payee", "Description", "Reference", "ChequeNumber"]
RECONCILE_PAGE = "https://momentum.xoro.one/Accounting/BankReconcile/BankReconcile.aspx"
ROUNDING_LIMIT = Decimal("1")      # anything smaller is cash rounding, not a real difference


class StatementError(RuntimeError):
    pass


def fiscal_year(d):
    """FY ends 31 July, so Aug-Dec roll into the next fiscal year."""
    return d.year + 1 if d.month > 7 else d.year


def month_folder(month):
    y, m = (int(x) for x in month.split("-"))
    return os.path.join(BASE, "FY%d" % fiscal_year(date(y, m, 1)), FOLDER,
                        "%02d %02d" % (y % 100, m))


def statement_lines(month, tz=None):
    """The month's statement lines, plus the balances they must span."""
    sessions = [s for s in pos.month_sessions(month) if s.get("closingTime")]
    if not sessions:
        raise StatementError("no closed POS register session for %s" % month)
    if len(sessions) > 1:
        raise StatementError(
            "%d closed sessions for %s (%s) — the statement assumes one month-end count"
            % (len(sessions), month, ", ".join(s["id"].rsplit("/", 1)[-1] for s in sessions)))
    return lines_for_session(sessions[0], month, tz or pos.shop_timezone())


def lines_for_session(s, month, tz):
    """The statement lines for one closed session. Pure — no API calls."""
    ok, variance, carried_in = pos.balances(s)
    if not ok:
        raise StatementError("session %s does not balance; refusing to build a statement"
                             % s["id"].rsplit("/", 1)[-1])
    if carried_in:
        raise StatementError(
            "session %s opened at %.2f against an expected %.2f — that %.2f is a PRIOR "
            "period correction and must be posted separately, not in this month"
            % (s["id"].rsplit("/", 1)[-1], pos._money(s["openingBalance"]),
               pos._money(s["expectedOpeningBalance"]), carried_in))

    end = _last_day(*(int(x) for x in month.split("-")))
    txns = [e["node"] for e in ((s.get("cashTransactions") or {}).get("edges") or [])]
    sales = Decimal("0")
    for t in txns:
        amt = Decimal(((t.get("amountSet") or {}).get("presentmentMoney") or {})["amount"])
        sales += -amt if (t.get("kind") or "").upper() in ("REFUND", "VOID") else amt
    reported = pos._money(s.get("netCashSales")) or Decimal("0")
    rounding = reported - sales

    lines = [{"date": end, "amount": sales, "description": "POS cash sales"}]
    if rounding:
        if abs(rounding) >= ROUNDING_LIMIT:
            raise StatementError(
                "cash difference %.2f is too large to treat as rounding — "
                "Shopify reports %.2f of cash sales but its transactions total %.2f"
                % (rounding, reported, sales))
        lines.append({"date": end, "amount": rounding, "description": "Penny rounding adjustment"})
    for e in ((s.get("adjustments") or {}).get("edges") or []):
        a = e["node"]
        lines.append({"date": pos._local(a["time"], tz),
                      "amount": Decimal(a["cash"]["amount"]),
                      "description": (a.get("note") or "Cash adjustment").strip()})
    if variance:
        lines.append({"date": end, "amount": variance, "description": "Cash count variance"})

    opening = pos._money(s["openingBalance"])
    closing = pos._money(s["closingBalance"])
    total = sum(l["amount"] for l in lines)
    if opening + total != closing:
        raise StatementError("lines total %.2f but %.2f -> %.2f needs %.2f"
                             % (total, opening, closing, closing - opening))
    lines.sort(key=lambda l: l["date"])
    return lines, opening, closing, s


def write_csv(month, lines):
    folder = month_folder(month)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "BankStatementImport.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        for l in lines:
            w.writerow([l["date"].isoformat(), "%.2f" % l["amount"], "",
                        l["description"], "", ""])
    return path


def carried_beginning_balance(client):
    """The balance Xoro will start the next reconciliation from.

    ``getLastReconcileHeaderDetailsFromAccountId`` reports ``beginningBal`` only for
    a COMPLETED reconciliation (StatusId 700); while one is in progress it returns
    0.0, so an already-open rec must be read through its stats instead — otherwise
    re-running this against an open rec looks like a balance mismatch.
    """
    last = client.get_last_reconcile_header(CASH_ACCOUNT["Id"]) or {}
    if last.get("StatusId") == 700:
        return last.get("beginningBal")
    rec_id = last.get("Id")
    if not rec_id:
        return None
    env = client.call(BANK_RECONCILE_SERVICE, "getReconcileStatsFromBankRecId", bankRecId=rec_id)
    return ((env or {}).get("Data") or {}).get("BeginningBalance")


def run(month, dry_run=False, open_rec=True):
    lines, opening, closing, session = statement_lines(month)
    client = WebMethodClient.from_config() if hasattr(WebMethodClient, "from_config") else WebMethodClient()

    prior = carried_beginning_balance(client)
    if prior is not None and Decimal(str(prior)) != opening:
        raise StatementError(
            "Xoro carries %.2f forward but the session counted %.2f at open — "
            "resolve before building the statement" % (Decimal(str(prior)), opening))

    end = _last_day(*(int(x) for x in month.split("-")))
    print("session %s | beginning %.2f -> ending %.2f (Xoro carries %.2f)"
          % (session["id"].rsplit("/", 1)[-1], opening, closing, Decimal(str(prior))))
    for l in lines:
        print("  %s %9s  %s" % (l["date"].isoformat(), "%.2f" % l["amount"], l["description"]))
    print("  %s %9s  = %.2f ending" % (" " * 10, "%.2f" % sum(l["amount"] for l in lines), closing))

    path = write_csv(month, lines)
    print("\nwrote %s" % path)

    if dry_run:
        print("(dry run — not uploaded, no reconciliation opened)")
        return None

    client.create_bank_statement(
        CASH_ACCOUNT["Id"],
        [{"date": l["date"].strftime("%m/%d/%Y"), "amount": float(l["amount"]),
          "description": l["description"]} for l in lines],
        start_date=date(end.year, end.month, 1).strftime("%m/%d/%Y"),
        end_date=end.strftime("%m/%d/%Y"),
        start_balance=float(opening), end_balance=float(closing),
    )
    print("uploaded %d statement line(s) to %s" % (len(lines), CASH_ACCOUNT["Name"]))

    if not open_rec:
        return None
    hdr = client.start_reconciliation(CASH_ACCOUNT["Id"], float(closing),
                                     end.strftime("%m/%d/%Y"),
                                     currency_id=CASH_ACCOUNT["CurrencyId"],
                                     beginning_balance=float(opening))
    rec_id = hdr.get("Id") if isinstance(hdr, dict) else hdr
    print("opened reconciliation %s, ending %.2f on %s" % (rec_id, closing, end.isoformat()))
    print(RECONCILE_PAGE)
    webbrowser.open(RECONCILE_PAGE)
    return rec_id


if __name__ == "__main__":
    argv = sys.argv[1:]
    flags = {a for a in argv if a.startswith("--")}
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)
    run(args[0], dry_run="--dry-run" in flags, open_rec="--no-rec" not in flags)
