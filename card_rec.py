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
import sys

DAYS = 5
CAP = 1000.0   # lines this size or bigger wait for the user (user, 2026-11-21); --include-large ticks them
TYPES = [{"DataType": "System.Int32", "FieldName": "TXN_TYPE_Id", "OperatorCode": 8, "FieldValue": v} for v in (101, 110)]


def _d(s):
    return dt.datetime.strptime(s[:10], "%m/%d/%Y").date()


def signed(line):
    """Statement amounts are unsigned; a debit is a negative movement on the account."""
    return round(-line["Amount"] if (line.get("TypeName") or "").lower() == "debit" else line["Amount"], 2)


def match(lines, txns, days=DAYS):
    """[(line, txn)], unmatched_lines, unmatched_txns. Pure: no Xoro calls (tested)."""
    free = list(txns)
    pairs, left = [], []
    for ln in sorted(lines, key=lambda l: _d(l["PostDate"])):
        amt, day = signed(ln), _d(ln["PostDate"])
        desc = (ln.get("Description") or "").strip().upper()
        cands = [t for t in free if round(t["AMOUNT"], 2) == amt and abs((_d(t["TXN_DATE"]) - day).days) <= days]
        if not cands:
            left.append(ln)
            continue
        # same description first; then a transaction dated on/before the bank date (money leaves PayPal before it
        # reaches the bank — a 10/26 credit is FT 10/23, not FT 10/28); then the nearest date
        best = min(cands, key=lambda t: (desc not in (t.get("MEMO") or "").upper(), _d(t["TXN_DATE"]) > day,
                                         abs((_d(t["TXN_DATE"]) - day).days)))
        free.remove(best)
        pairs.append((ln, best))
    return pairs, left, free


def review(pairs, cap=CAP):
    """Split matches into (auto, held). Held: |amount| >= cap, or Xoro suggested a *different* transaction than ours
    (its MatchedTxnId) — those need a look before ticking. Pure (tested)."""
    auto, held = [], []
    for ln, t in pairs:
        why = []
        if abs(signed(ln)) >= cap:
            why.append(">= $%d" % cap)
        if ln.get("MatchedTxnId") and ln["MatchedTxnId"] != t.get("TXN_ID"):
            why.append("Xoro suggests %s" % ln.get("MatchedRefNumber"))
        (held if why else auto).append((ln, t, ", ".join(why)))
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
    for ln, t, why in held:
        print("  REVIEW    %s %10.2f  %-40s -> %s %s  (%s)" % (ln["PostDate"], signed(ln), (ln.get("Description") or "")[:40],
                                                         t.get("REF_NUMBER"), t.get("TXN_DATE"), why))
    for ln in left:
        print("  UNMATCHED %s %10.2f  %s" % (ln["PostDate"], signed(ln), (ln.get("Description") or "")[:50]))
    if not commit:
        print("dry run — pass --commit to tick the %d line(s)" % len(auto))
        return
    for ln, t, _ in auto:
        obj = [{"accntTxnId": t["TXN_ID"], "tblRefId": t["REF_ID"], "txnTypeId": t["TXN_TYPE_ID"],
                "fAccountingId": t["ACCOUNT_ID"], "amount": t["AMOUNT"], "txnTableId": t["ID"]}]
        c.call("ConnectBankWebMethods", "reconcileBankAccountLine", bnkTxnId=str(ln["Id"]),
               statementId=str(ln["BankStatementId"]), accntTxnOBjList=json.dumps(obj),
               multipleTranFlag="true", bankRecHeaderId=str(rec_id))
    st = c.call("BankReconcileWebMethods", "getReconcileStatsFromBankRecId", bankRecId=str(rec_id))
    st = st.get("Data", st)
    print("ticked %d. Difference now %s%s." % (len(auto), st.get("Difference"), " (held lines still to tick)" if held else ""))
    print("Not finished — click Finish in Xoro once the difference is 0.00.")


if __name__ == "__main__":
    run(sys.argv[1], commit="--commit" in sys.argv, include_large="--include-large" in sys.argv)
