"""Tick a card (or bank) reconciliation in Xoro: match each uploaded statement line to its Xoro transaction.

    python3 card_rec.py 2105            # dry run: show the matches for 2105's open reconciliation
    python3 card_rec.py 2105 --commit   # tick them; never clicks Finish
    Lines of $1,000+ and lines where Xoro's suggested match differs from ours are held for review.

Calls recorded from the user ticking rec 1005 by hand (2026-11-21):
  statement lines  ConnectBankWebMethods.getBankTransactionsToReconcile  (Amount > 0, TypeName debit/credit)
  Xoro side        ConnectBankWebMethods.searchTransactionToReconcile     (AMOUNT signed: a card charge is negative)
  tick one line    ConnectBankWebMethods.reconcileBankAccountLine(bnkTxnId, statementId, accntTxnOBjList, ...)
  progress         BankReconcileWebMethods.getReconcileStatsFromBankRecId(bankRecId)

Matching: same signed amount, Xoro date within DAYS of the statement date; among several, the same description
(card JEs carry the statement description as memo), then the nearest date. Anything ambiguous or unmatched is
listed and left for the user.
"""

import datetime as dt
import json
import re
import sys

DAYS = 5
CAP = 1000.0   # lines this size or bigger wait for the user (user, 2026-11-21); --include-large ticks them
TYPES = [{"DataType": "System.Int32", "FieldName": "TXN_TYPE_Id", "OperatorCode": 8, "FieldValue": v} for v in (101, 110)]


def _d(s):
    return dt.datetime.strptime(s[:10], "%m/%d/%Y").date()


def signed(line):
    """Statement amounts are unsigned; a debit is a negative movement on the account."""
    return round(-line["Amount"] if (line.get("TypeName") or "").lower() == "debit" else line["Amount"], 2)


PREPAY_DAYS = 120   # prepayments get entered when the order is placed, weeks before the wire leaves


def _first_word(name):
    w = re.findall(r"[A-Z]{4,}", (name or "").upper())
    return w[0] if w else None


def match(lines, txns, days=DAYS):
    """[(line, [txn, ...])], unmatched_lines, unmatched_txns. Pure: no Xoro calls (tested).

    1. one transaction: same signed amount within `days` (prefer same description, then dated on/before the bank
       date, then nearest); or a vendor prepayment of the same amount up to PREPAY_DAYS earlier whose vendor's name
       appears in the bank description (Ronda 8/24 prepayment, 10/28 wire).
    2. two transactions summing to the line within `days` — a wire that arrived $25 short ticks its transfer and the
       separate fee JE together.
    """
    free = list(txns)
    pairs, left = [], []
    for ln in sorted(lines, key=lambda l: _d(l["PostDate"])):
        amt, day = signed(ln), _d(ln["PostDate"])
        desc = (ln.get("Description") or "").strip().upper()
        near = lambda t: abs((_d(t["TXN_DATE"]) - day).days) <= days
        cands = [t for t in free if round(t["AMOUNT"], 2) == amt and near(t)]
        if not cands:
            cands = [t for t in free if round(t["AMOUNT"], 2) == amt and "PREPAYMENT" in (t.get("TXN_TYPE_NAME") or "").upper()
                     and 0 <= (day - _d(t["TXN_DATE"])).days <= PREPAY_DAYS
                     and _first_word(t.get("ENTITY_NAME")) and _first_word(t.get("ENTITY_NAME")) in desc]
        if cands:
            best = min(cands, key=lambda t: (desc not in (t.get("MEMO") or "").upper(), _d(t["TXN_DATE"]) > day,
                                             abs((_d(t["TXN_DATE"]) - day).days)))
            free.remove(best)
            pairs.append((ln, [best]))
        else:
            left.append(ln)
    still = []
    for ln in left:
        amt, day = signed(ln), _d(ln["PostDate"])
        near = [t for t in free if abs((_d(t["TXN_DATE"]) - day).days) <= days]
        combo = next(((a, b) for i, a in enumerate(near) for b in near[i + 1:]
                      if round(a["AMOUNT"] + b["AMOUNT"], 2) == amt), None)
        if combo:
            for t in combo:
                free.remove(t)
            pairs.append((ln, list(combo)))
        else:
            still.append(ln)
    return pairs, still, free


def review(pairs, cap=CAP):
    """Split matches into (auto, held). Held: |amount| >= cap, or Xoro suggested a *different* transaction than ours
    (its MatchedTxnId) — those need a look before ticking. Pure (tested)."""
    auto, held = [], []
    for ln, ts in pairs:
        why = []
        if abs(signed(ln)) >= cap:
            why.append(">= $%d" % cap)
        if len(ts) > 1:
            why.append("%d entries" % len(ts))
        elif ts[0].get("TXN_TYPE_NAME", "").upper().endswith("PREPAYMENT") and abs((_d(ts[0]["TXN_DATE"]) - _d(ln["PostDate"])).days) > DAYS:
            why.append("prepayment dated %s" % ts[0]["TXN_DATE"])
        t = ts[0]
        if len(ts) == 1 and ln.get("MatchedTxnId") and ln["MatchedTxnId"] != t.get("TXN_ID"):
            why.append("Xoro suggests %s" % ln.get("MatchedRefNumber"))
        (held if why else auto).append((ln, ts, ", ".join(why)))
    return auto, held


def _rows(r):
    d = r.get("Data", r) if isinstance(r, dict) else r
    return d.get("rows", d) if isinstance(d, dict) else d


def run(gl, commit=False, include_large=False):
    from xoro_webmethods import WebMethodClient
    c = WebMethodClient.from_config()
    acct = next(a for a in c.get_bank_statement_accounts() if a["Name"].startswith(gl + " "))
    fid = acct["FAccountingId"]
    hdr = c.get_last_reconcile_header(fid)
    if hdr.get("StatusId") == 700:
        sys.exit("%s has no open reconciliation (last one %s is finished)" % (acct["Name"], hdr.get("Id")))
    rec_id, end = hdr["Id"], hdr["lastStatementDate"]
    lines = _rows(c.call("ConnectBankWebMethods", "getBankTransactionsToReconcile", size="1000", number=1, sorder="desc",
                         fAccountingId=fid, sname="POST_DATE", xoroTxnStrExpressions=json.dumps(TYPES),
                         strExpressions=json.dumps([{"DataType": "System.DateTime", "FieldName": "POST_DATE",
                                                     "OperatorCode": 5, "FieldValue": end}])))
    # MatchedFlag only means Xoro *suggests* a match (MatchedTxnId) — those lines are still unticked, so keep them
    # all; the reconcile stats below are the truth for what's cleared.
    txns = _rows(c.call("ConnectBankWebMethods", "searchTransactionToReconcile", size="2000", number=1, sorder="desc",
                        fAccountingId=fid, sname="TXN_DATE", strExpressions=json.dumps(TYPES)))
    txns = [t for t in txns if not t.get("RECONCILED_FLAG")]
    pairs, left, _free = match(lines, txns)
    auto, held = review(pairs, cap=float("inf") if include_large else CAP)
    print("%s  rec %s ending %s: %d open line(s): %d to tick, %d held for review, %d unmatched"
          % (acct["Name"], rec_id, end, len(lines), len(auto), len(held), len(left)))
    for ln, ts, why in held:
        print("  REVIEW    %s %10.2f  %-40s -> %s  (%s)" % (ln["PostDate"], signed(ln), (ln.get("Description") or "")[:40],
                                                      " + ".join("%s %s" % (t.get("REF_NUMBER"), t.get("TXN_DATE")) for t in ts), why))
    for ln in left:
        print("  UNMATCHED %s %10.2f  %s" % (ln["PostDate"], signed(ln), (ln.get("Description") or "")[:50]))
    if not commit:
        print("dry run — pass --commit to tick the %d line(s)" % len(auto))
        return
    for ln, ts, _ in auto:
        obj = [{"accntTxnId": t["TXN_ID"], "tblRefId": t["REF_ID"], "txnTypeId": t["TXN_TYPE_ID"],
                "fAccountingId": t["ACCOUNT_ID"], "amount": t["AMOUNT"], "txnTableId": t["ID"]} for t in ts]
        c.call("ConnectBankWebMethods", "reconcileBankAccountLine", bnkTxnId=str(ln["Id"]),
               statementId=str(ln["BankStatementId"]), accntTxnOBjList=json.dumps(obj),
               multipleTranFlag="true", bankRecHeaderId=str(rec_id))
    st = c.call("BankReconcileWebMethods", "getReconcileStatsFromBankRecId", bankRecId=str(rec_id))
    st = st.get("Data", st)
    print("ticked %d. Difference now %s%s." % (len(auto), st.get("Difference"), " (held lines still to tick)" if held else ""))
    print("Not finished — click Finish in Xoro once the difference is 0.00.")


if __name__ == "__main__":
    run(sys.argv[1], commit="--commit" in sys.argv, include_large="--include-large" in sys.argv)
