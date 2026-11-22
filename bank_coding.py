"""Code a bank reconciliation's unexplained lines the way past months did, as one JE per line.

    python3 bank_coding.py 1160 <out_dir>            # dry run: writes <out_dir>/coding.csv and prints the JE chart
    python3 bank_coding.py 1160 <out_dir> --commit   # posts the rows the user approved in coding.csv

Lines that already match a Xoro transaction (deposits, transfers, bill payments) are left to `card_rec.py`;
only the unmatched ones are coded. Each takes the split its payee got most often over the last 12 months of
JEs on this bank account (card_coding.history). Outgoing supplier wires usually pay a bill, so they are
flagged instead of coded.
"""
import csv
import datetime as dt
import json
import os
import re
import sys

import card_coding as cc
import card_rec as cr

FIELDS = ["date", "description", "amount", "key", "source", "confidence", "template", "split", "account", "gst", "note"]


def bank_key(desc):
    """BMO/Umpqua descriptions put the payee after the last comma ('Pre-Authorized Payment , MANULIFE  ...')."""
    d = (desc or "").split(" | ")[0]
    d = d.split(",")[-1] if "," in d else d
    d = re.sub(r"\b(RLS/LOY|BPY/FAC|MSP/DIV|PRO/PRO|BOM/B/M|INS/ASS|PAY/PAY|ST\.|ST-\S+)\b", " ", d.upper())
    return cc.merchant_key(d)


def unmatched_lines(c, gl):
    acct = next(a for a in c.get_bank_statement_accounts() if a["Name"].startswith(gl + " "))
    fid = acct["FAccountingId"]
    hdr = c.get_last_reconcile_header(fid)
    lines = cr._rows(c.call("ConnectBankWebMethods", "getBankTransactionsToReconcile", size="1000", number=1,
                            sorder="desc", fAccountingId=fid, sname="POST_DATE",
                            xoroTxnStrExpressions=json.dumps(cr.TYPES),
                            strExpressions=json.dumps([{"DataType": "System.DateTime", "FieldName": "POST_DATE",
                                                        "OperatorCode": 5, "FieldValue": hdr["lastStatementDate"]}])))
    txns = cr._rows(c.call("ConnectBankWebMethods", "searchTransactionToReconcile", size="2000", number=1,
                           sorder="desc", fAccountingId=fid, sname="TXN_DATE", strExpressions=json.dumps(cr.TYPES)))
    txns = [t for t in txns if not t.get("RECONCILED_FLAG")]
    _pairs, left, _free = cr.match(lines, txns)
    return acct, hdr, left


def suggest(gl, out_dir):
    from xoro_webmethods import WebMethodClient
    c = WebMethodClient.from_config()
    acct, hdr, left = unmatched_lines(c, gl)
    end = dt.datetime.strptime(hdr["lastStatementDate"], "%m/%d/%Y").date()
    hist = cc.history(gl, end, client=None, key=bank_key)
    rows = []
    for ln in sorted(left, key=lambda l: cr._d(l["PostDate"])):
        desc, amt = (ln.get("Description") or "").strip(), cr.signed(ln)
        k = bank_key(desc)
        row = dict(date=ln["PostDate"], description=desc, amount="%.2f" % -amt, key=k, source="", confidence="",
                   template="", split="", account="", gst="", note="")
        if "OUTGOING WIRE" in desc.upper():
            row.update(source="bill?", account="skip", note="supplier wire - pay the bill (bill payment), not a JE")
        elif k in hist:
            sigs = hist[k]
            best = max(sigs.values(), key=lambda s: s["n"])
            row.update(source="history", confidence="%d/%d" % (best["n"], sum(s["n"] for s in sigs.values())),
                       template=best["ref"],
                       split=" + ".join("%s %d%%" % (t["gl"], round(t["share"] * 100)) for t in best["tmpl"]))
        else:
            row.update(source="unknown")
        rows.append(row)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "coding.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    names = {}
    for sigs in hist.values():
        for s in sigs.values():
            for t in s["tmpl"]:
                names[t["gl"]] = t["name"]
    print("%s rec %s: %d unexplained line(s) -> %s" % (acct["Name"], hdr["Id"], len(rows), path))
    print("%-10s %-38s %11s  %-9s %s" % ("date", "payee", "amount", "source", "JE (Dr ... / Cr %s)" % gl))
    for r in rows:
        je = r["split"] or r["account"] or "?"
        if r["split"]:
            je = " + ".join("%s %s" % (p.split()[0], names.get(p.split()[0], "").split(" - ", 1)[-1][:22]) + " " + p.split()[1]
                            for p in r["split"].split(" + "))
        print("%-10s %-38s %11s  %-9s %s%s" % (r["date"], r["description"].split(",")[-1].strip()[:38], r["amount"],
                                              r["source"], je, ("  [" + r["note"] + "]") if r["note"] else ""))
    return path, rows


if __name__ == "__main__":
    suggest(sys.argv[1], sys.argv[2])
