"""File a Xoro BankReconciliationReport PDF into its account's month folder and tick its Todoist task.

usage: python3 file_rec.py <pdf> [YY MM]      (default: last month)

Ticks the Job Overview task only when the report is Reconciled with $0.00 uncleared;
otherwise it files the PDF and stops, so the uncleared items can be reviewed first
(old carry-overs such as FY year-end AJEs are normal - tick by hand once checked)."""
import os, re, sys, json, shutil, urllib.request
import pdfplumber

import datetime
_last = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
pdf = sys.argv[1]; month = sys.argv[2] if len(sys.argv) > 2 else _last.strftime("%y %m")
AD = "/Users/angus/Library/CloudStorage/OneDrive-St.MoritzWatch/Accounting Docs"
_fy = 2000 + int(month[:2]) + (1 if int(month[3:]) >= 8 else 0)  # FY ends July 31
CC = AD + "/CC Expenses/YE %d/" % _fy; BR = AD + "/Bank Reconciliations/FY%d/" % _fy
MAP = [  # (pattern in report title, folder, Todoist task)
    (r"Bank of America Visa 9180", CC + "BOA VISA USD 9180", "BOA Visa 9180"),
    (r"American Express Blue 1005", CC + "Amex USD Blue 1005", "Amex Blue 1005"),
    (r"American Express Delta 1003", CC + "Amex USD Delta 1003", "Amex Delta 1003"),
    (r"American Express Sofia 4002", CC + "Amex Sofia 4002", "Amex Sofia 4002"),
    (r"American Express 4009", CC + "Amex Corporate 4009", "Amex Corp 4009"),
    (r"CIBC", CC + "CIBC MC 2244", "CIBC MC 2244"),
    (r"TD Visa 0926", CC + "TD VISA 0926", "TD Visa 0926"),
    (r"BMO 41547651", BR + "BMO CAD", "BMO CAD"),
    (r"BMO 44569097", BR + "BMO USD", "BMO USD"),
    (r"Umpqua", BR + "Umpqua SMWCorp", "Umpqua Corp 1140"),
    (r"Wise \(EUR\)", BR + "Wise EUR", "Wise EUR"),
    (r"Cash - Service Centre", BR + "Service Centre Cash", "Shopify Service Centre Cash"),
    (r"Paypal USD", BR + "Paypal", "PayPal USD"),
]
t = "\n".join(p.extract_text() or "" for p in pdfplumber.open(pdf).pages)
title = (re.search(r"Reconciliation Report\n(.+)", t) or [None, ""])[1]
status = (re.search(r"Status (\w+)", t) or [None, "?"])[1]
uncleared = (re.search(r"Uncleared Transactions as of \S+ (\(?\$[\d,.]+\)?)", t) or [None, "?"])[1]
recno = (re.search(r"Reconciliation No (\S+)", t) or [None, "?"])[1]
print("report:", title, "|", recno, "|", status, "| uncleared", uncleared)
hit = next(((f, task) for pat, f, task in MAP if re.search(pat, title)), None)
if not hit:
    sys.exit("no folder mapping for this account — not filed")
folder, task = hit
dest_dir = os.path.join(folder, month); os.makedirs(dest_dir, exist_ok=True)
dest = os.path.join(dest_dir, os.path.basename(pdf))
n = 2
while os.path.exists(dest):
    base, ext = os.path.splitext(os.path.basename(pdf)); dest = os.path.join(dest_dir, "%s (%d)%s" % (base, n, ext)); n += 1
shutil.move(pdf, dest); print("filed:", dest.replace(AD + "/", ""))
if status != "Reconciled" or uncleared != "$0.00":
    sys.exit("NOT ticking '%s' in Todoist: status %s, uncleared %s" % (task, status, uncleared))
tok = next(l.split("=", 1)[1].strip() for l in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")) if l.startswith("TODOIST_API_TOKEN="))
H = {"Authorization": "Bearer " + tok}
get = lambda u: json.load(urllib.request.urlopen(urllib.request.Request("https://api.todoist.com/api/v1/" + u, headers=H)))
pid = [p for p in get("projects?limit=200")["results"] if p["name"] == "Job Overview"][0]["id"]
ts = [x for x in get("tasks?project_id=%s&limit=200" % pid)["results"] if x["content"] == task]
if not ts:
    sys.exit("Todoist task '%s' not open (already ticked?)" % task)
urllib.request.urlopen(urllib.request.Request("https://api.todoist.com/api/v1/tasks/%s/close" % ts[0]["id"], headers=H, method="POST"))
print("ticked in Todoist:", task)
