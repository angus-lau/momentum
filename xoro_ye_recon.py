#!/usr/bin/env python3
"""
Pull each bank account's reconciliation position as at a date, straight from Xoro.

Fills the gap where a month's folder has no `BankReconciliationReport` PDF: this
is the DATA behind that report (`BankReconcileWebMethods.getBankRcReportDataByAccntAndDate`,
params `bankFaccntId` / `date` / `asOfFlag`), not a copy of the PDF itself — the
PDF is rendered by the Xoro UI and is not exposed by the service.

For each account it writes, into `<out>/<account>/`:
  * `xoro-recon-<date>.txt` — the summary: reconciled bank vs ERP balance, the
    difference, and the unreconciled totals
  * `xoro-outstanding-<date>.csv` — every transaction still unreconciled at the
    date, which is the part a year-end file actually needs

Usage:
    python3 xoro_ye_recon.py 2026-07-31
    python3 xoro_ye_recon.py 2026-07-31 --out ~/Desktop/"YE 2026 Bank Files"
"""

import csv
import os
import sys
from datetime import date
from decimal import Decimal

from xoro_webmethods import BANK_RECONCILE_SERVICE, WebMethodClient

# folder name -> Xoro FAccountingId. Folder names match the OneDrive layout.
ACCOUNTS = [
    ("Service Centre Cash", "B7D04105A81A0E8E01A93DB24565", "1121 - Cash - Service Centre"),
    ("Umpqua SMWCorp",      "B7D04105A81AED1CB3EA3AB9426A", "1140 - Umpqua Bank 1729 (USD)"),
    ("BMO CAD",             "72FF68D10C373530638D3162C4127", "1160 - BMO 41547651 (CAD)"),
    ("BMO USD",             "72FF68D10C3CFB73A65FAFF134003", "1170 - BMO 44569097 (USD)"),
    ("Wise CAD",            "B8169CEC7955C61EC406CE4E4103", "1180 - Wise (CAD)"),
    ("Wise EUR",            "B816D58A97190A714612C72141CA", "1195 - Wise (EUR)"),
    ("Wise GBP",            "B8169CEFC6DD5405D50B050F4B27", "1190 - Wise (GBP)"),
    ("Paypal",              "B7D04105A81AC13AE701924645D2", "1143 - Paypal USD"),
]

SUMMARY_FIELDS = [
    ("ReconciledBankBalance", "Reconciled bank balance"),
    ("ReconciledERPBalance", "Reconciled ERP (book) balance"),
    ("UnReconciledBankBalance", "Unreconciled bank"),
    ("UnReconciledERPBalance", "Unreconciled ERP"),
    ("DebitTotalERP", "Unreconciled debits (ERP)"),
    ("CreditTotalERP", "Unreconciled credits (ERP)"),
    ("NetTotalBank", "Net bank"),
    ("NetTotalERP", "Net ERP"),
]
TXN_COLUMNS = ["TxnDate", "TxnTypeName", "RefNumber", "EntityFullName", "Amount",
               "CurrencyName", "Memo", "CreateDttm", "CreateSource"]


def fetch(client, faccount_id, when):
    """``when`` as MM/DD/YYYY. asOfFlag False = the reconciliation view at that date."""
    env = client.call(BANK_RECONCILE_SERVICE, "getBankRcReportDataByAccntAndDate",
                      bankFaccntId=faccount_id, date=when, asOfFlag=False)
    return (env or {}).get("Data") if isinstance(env, dict) else None


def outstanding(data):
    """Unreconciled transactions, both directions, flattened."""
    rows = []
    for key in ("DebitTransERP", "CreditTransERP", "DebitTrans", "CreditTrans", "BankTrans"):
        for t in (data.get(key) or []):
            rows.append({"_source": key, **{c: t.get(c) for c in TXN_COLUMNS}})
    rows.sort(key=lambda r: (str(r.get("TxnDate") or ""), str(r.get("RefNumber") or "")))
    return rows


def _d(v):
    return Decimal(str(v)) if v is not None else Decimal(0)


def run(when_iso, out_dir):
    when = date.fromisoformat(when_iso).strftime("%m/%d/%Y")
    client = WebMethodClient.from_config() if hasattr(WebMethodClient, "from_config") else WebMethodClient()
    print("%-22s %14s %14s %12s  %s" % ("account", "bank", "ERP(book)", "difference", "outstanding"))
    results = []
    for folder, aid, label in ACCOUNTS:
        try:
            data = fetch(client, aid, when)
        except Exception as e:
            print("%-22s ERROR %s" % (folder, str(e)[:60]))
            continue
        if not data:
            print("%-22s no data returned" % folder)
            continue
        bank, erp = _d(data.get("ReconciledBankBalance")), _d(data.get("ReconciledERPBalance"))
        rows = outstanding(data)
        print("%-22s %14.2f %14.4f %12.4f  %d item(s)" % (folder, bank, erp, bank - erp, len(rows)))

        d = os.path.join(out_dir, folder)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "xoro-recon-%s.txt" % when_iso), "w") as f:
            f.write("%s\nXoro reconciliation position as at %s\n\n" % (label, when_iso))
            for k, lbl in SUMMARY_FIELDS:
                f.write("%-34s %16.4f\n" % (lbl, _d(data.get(k))))
            f.write("%-34s %16.4f\n" % ("Bank less ERP", bank - erp))
            f.write("\nUnreconciled items: %d\n" % len(rows))
        with open(os.path.join(d, "xoro-outstanding-%s.csv" % when_iso), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["_source"] + TXN_COLUMNS)
            w.writeheader()
            w.writerows(rows)
        results.append((folder, bank, erp, len(rows)))
    return results


# ---- latest reconciliation report ---------------------------------------

STATUS = {700: "Completed", 10: "In progress"}

STAT_FIELDS = [
    ("BeginningBalance", "Beginning balance"),
    ("ClearedDeposits", "Deposits and credits cleared"),
    ("ClearedPayments", "Cheques and payments cleared"),
    ("ClearedBalance", "Cleared balance"),
    ("EndingBalance", "Statement ending balance"),
    ("Difference", "Difference"),
]


def latest_rec(client, faccount_id):
    """The account's most recent reconciliation: (header, stats)."""
    hdr = client.get_last_reconcile_header(faccount_id) or {}
    rec_id = hdr.get("Id")
    if not rec_id:
        return hdr, {}
    env = client.call(BANK_RECONCILE_SERVICE, "getReconcileStatsFromBankRecId", bankRecId=rec_id)
    return hdr, ((env or {}).get("Data") or {})


def write_latest_rec(client, folder, faccount_id, label, out_dir):
    """Write the most recent reconciliation as a readable report.

    This is Xoro's reconciliation *data*, not its PDF — the PDF is rendered by the
    UI and the service does not expose it. Used for accounts whose month folder
    has no `BankReconciliationReport` on file.
    """
    hdr, stats = latest_rec(client, faccount_id)
    if not stats:
        return None
    ending = stats.get("EndingDate") or hdr.get("lastStatementDate") or ""
    if "/" in ending:                      # Xoro gives MM/DD/YYYY
        mm, dd, yyyy = ending.split("/")
        iso = "%s-%s-%s" % (yyyy, mm, dd)
    else:
        iso = ending.replace("/", "-")
    status = STATUS.get(hdr.get("StatusId"), "status %s" % hdr.get("StatusId"))
    d = os.path.join(out_dir, folder)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "xoro-latest-reconciliation-%s.txt" % iso)
    with open(path, "w") as f:
        f.write("%s\n" % label)
        f.write("Most recent reconciliation in Xoro\n\n")
        f.write("%-32s %s\n" % ("Reconciliation No", hdr.get("Id")))
        f.write("%-32s %s\n" % ("Status", status))
        f.write("%-32s %s\n" % ("Period ending", ending))
        f.write("%-32s %s\n" % ("Currency", stats.get("CurrencyCode")))
        f.write("\n")
        for k, lbl in STAT_FIELDS:
            v = stats.get(k)
            f.write("%-32s %16.2f\n" % (lbl, _d(v)))
        f.write("%-32s %16d\n" % ("  deposits cleared (count)", stats.get("ClearedDepositsCount") or 0))
        f.write("%-32s %16d\n" % ("  payments cleared (count)", stats.get("ClearedPaymentsCount") or 0))
        if _d(stats.get("Difference")) != 0:
            f.write("\n*** This reconciliation does not balance. ***\n")
        f.write("\nSource: BankReconcileWebMethods.getReconcileStatsFromBankRecId "
                "(bankRecId=%s).\nThis is the report's underlying data; Xoro's PDF is "
                "rendered by the UI and is not exposed by the service.\n" % hdr.get("Id"))
    return path, hdr, stats


if __name__ == "__main__":
    argv = sys.argv[1:]
    out = os.path.expanduser("~/Desktop/YE 2026 Bank Files")
    if "--out" in argv:
        i = argv.index("--out")
        out = os.path.expanduser(argv[i + 1])
        del argv[i:i + 2]
    if "--latest-rec" in argv:
        only = [a for a in argv if not a.startswith("--")]
        client = (WebMethodClient.from_config() if hasattr(WebMethodClient, "from_config")
                  else WebMethodClient())
        for folder, aid, label in ACCOUNTS:
            if only and folder not in only:
                continue
            r = write_latest_rec(client, folder, aid, label, out)
            if not r:
                print("%-22s no reconciliation found" % folder)
                continue
            path, hdr, stats = r
            print("%-22s rec %-5s %-12s ending %-11s diff %10.2f  -> %s"
                  % (folder, hdr.get("Id"), STATUS.get(hdr.get("StatusId"), hdr.get("StatusId")),
                     stats.get("EndingDate"), _d(stats.get("Difference")),
                     os.path.basename(path)))
        sys.exit(0)
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)
    run(args[0], out)
