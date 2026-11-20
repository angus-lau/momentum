"""Code card statement lines to GL accounts and post them as journal entries.

    python3 card_coding.py suggest cibc "26 10"            # writes <month folder>/coding.csv
    python3 card_coding.py post cibc "26 10"               # dry-run: prints the JEs
    python3 card_coding.py post cibc "26 10" --commit      # posts them, one JE per line

The posted JE copies how the card was booked by hand: dated the transaction date, memo = the card
description, Dr expense (+ Dr 2226 GST) / Cr the card account.

suggest: each line gets the split the same merchant got most often over the last 12 months on this
card (e.g. Costco = 50% 2900 / 50% 7620 + GST). Restaurants always go to 7320, never 2900 (user,
2026-11-19). Unseen merchants come out blank with source "unknown". Claude fills those by a web
search and marks them "search" for the user to review. Payments are skipped: they match the
transfer in the rec.

Editing coding.csv: put a GL code in `account` (and `gst` Y / GP / N) to override the history split.
Leave `account` blank to keep `template`. Put `skip` in `account` to leave a line alone.
"""

import collections
import copy
import csv
import datetime as dt
import json
import os
import re
import sys

import convert_activity as ca
from xoro_api import XoroClient, exchange_rate_for

PROCESSORS = {"SQ", "TST", "HCM", "BAM", "PAYPAL", "SP", "DD", "LS"}
RESTAURANT = re.compile(r"RESTAURAN|\bPHO\b|KITCHEN|BISTRO|SUSHI|PIZZ|GRILL|CAFE|COFFEE|BAKE|CATERI|"
                        r"TIMEOUT|CACTUS CLUB|PROVISIONS|CHOICES|LOBLAWS|DINER|RAMEN|IZAKAYA|TAQUER|NOODLE|DUMPLING|BURGER|BRASSERIE|EATERY", re.I)
CARRIER = re.compile(r"FEDEX|LIVINGSTON|LIV INTL|\bDHL\b", re.I)   # landed cost / DHL flow, not a JE
PAYMENT = re.compile(r"PAYMENT.*THANK YOU|PAIEMENT", re.I)
GST_RATE = 0.05
MEALS = "7320"
FIELDS = ["date", "description", "amount", "key", "source", "confidence", "template", "split",
          "account", "gst", "note"]


def merchant_key(desc):
    """Stable merchant name: 'FACEBK *T7QQ MENLO PARK, CA' -> 'FACEBK', 'HCM*PHO VIET' -> 'PHO VIET'."""
    d = desc.upper().split(",")[0]
    if "*" in d:
        head, tail = d.split("*", 1)
        head = head.strip().rstrip("-")
        d = tail if head in PROCESSORS else head
    d = re.sub(r"^(TST-|LS )", "", d.strip())
    words = []
    for t in re.split(r"[\s#]+", d):
        if re.search(r"\d", t):
            lead = re.match(r"[A-Z]{5,}", t)   # IMPARK00011928U -> IMPARK; TRJ83HRPJQZX dropped
            t = lead.group(0) if lead else ""
        t = re.sub(r"[^A-Z&']", "", t)
        if len(t) >= 2:
            words.append(t)
    return " ".join(words[:2])


def _months_back(end, n):
    y, m = end.year, end.month
    for _ in range(n):
        start = dt.date(y, m, 1)
        nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
        yield start, min(nxt - dt.timedelta(days=1), end)
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)


def history(card_gl, end, months=12, client=None):
    """{merchant key: Counter(split signature -> [JE lines template, ref])} from past card JEs."""
    client = client or XoroClient()
    rows = []
    for a, b in _months_back(end, months):
        rows += client.get_gl_transactions(a.isoformat(), b.isoformat(), account_gl_codes=card_gl)
    jes = collections.defaultdict(list)
    for r in rows:
        if r.get("TxnTypeName") == "Journal Entry":
            jes[r["RefNumber"]].append(r)
    hist = collections.defaultdict(dict)
    for ref, lines in jes.items():
        card = [l for l in lines if l["GLCode"] == card_gl]
        other = [l for l in lines if l["GLCode"] != card_gl]
        if len(card) != 1 or not other:
            continue
        total = -float(card[0]["Amount"])
        if abs(total) < 0.01:
            continue
        tmpl = [{"share": float(l["Amount"]) / total, "gl": l["GLCode"], "id": l["F_AccountingId"],
                 "name": l["F_AccountingName"], "type": l["F_AccountingTypeId"],
                 "entity": l.get("EntityAccountId") or "", "entity_name": l.get("EntityFullName") or ""}
                for l in other]
        sig = tuple(sorted((t["gl"], round(t["share"], 2)) for t in tmpl))
        slot = hist[merchant_key(card[0]["Memo"] or "")].setdefault(sig, {"n": 0, "tmpl": tmpl, "ref": ref})
        slot["n"] += 1
    return hist


def suggest(key, month, client=None):
    acct = ca.ACCOUNTS[key]
    folder = ca.get_month_folder(key, month)
    lines, _ = ca.convert_to_lines(acct.get("bank_config", key), ca.find_activity_file(folder),
                                   dhl_filter=acct.get("dhl_filter", False))   # DHL has its own pass
    card_gl = acct["option"].split(" ")[0]
    end = max(_date(l["date"]) for l in lines)
    hist = history(card_gl, end, client=client)
    out = []
    for l in lines:
        desc, amt, date = _line_fields(l)
        k = merchant_key(desc)
        row = dict(date=date, description=desc, amount="%.2f" % -amt, key=k, source="", confidence="",
                   template="", split="", account="", gst="", note="")
        if PAYMENT.search(desc):
            row.update(source="payment", account="skip", note="card payment: match the transfer in the rec")
        elif CARRIER.search(desc):
            row.update(source="carrier", account="skip", note="landed cost (2145) or DHL flow")
        elif RESTAURANT.search(desc):
            row.update(source="rule", account=MEALS, gst="N", note="restaurant -> 7320")
        elif k in hist:
            sigs = hist[k]
            best = max(sigs.values(), key=lambda s: s["n"])
            n = sum(s["n"] for s in sigs.values())
            row.update(source="history", confidence="%d/%d" % (best["n"], n), template=best["ref"],
                       split=" + ".join("%s %d%%" % (t["gl"], round(t["share"] * 100)) for t in best["tmpl"]))
        else:
            row.update(source="unknown")
        out.append(row)
    path = os.path.join(folder, "coding.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(out)
    return path, out


def _line_fields(l):
    """(description, signed amount, MM/DD/YYYY) from a convert_to_lines row; charges are negative."""
    return l["description"], float(l["amount"]), _date(l["date"]).strftime("%m/%d/%Y")


def _date(s):
    """Card exports differ: 2026-10-20, 10/20/2026, 20 Oct 2026."""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError("unknown date format: %r" % s)


# ---------- posting ----------

def _round_lines(parts, total):
    """Round each part to cents and put the rounding cent on the largest non-tax line."""
    for p in parts:
        p["amt"] = round(p["amt"], 2)
    diff = round(total - sum(p["amt"] for p in parts), 2)
    if diff:
        max((p for p in parts if p["gl"] != "2226"), key=lambda p: abs(p["amt"]))["amt"] += diff
    return parts


def build_parts(row, hist_by_ref, accounts, gst_line):
    """The debit side of one card line: [{gl, id, name, type, entity, amt}], summing to the charge."""
    total = float(row["amount"])
    a = (row["account"] or "").strip()
    if a:
        acc = accounts[a]
        base = {"gl": a, "id": acc["FAccountingId"], "name": acc["ExtendedName"], "type": acc["FAccountTypeId"],
                "entity": "", "entity_name": ""}
        g = row["gst"].strip().upper()
        if g in ("Y", "GP"):   # Y = GST only; GP = GST + 7% BC PST (PST stays in the expense)
            gst = total * GST_RATE / (1 + GST_RATE + (0.07 if g == "GP" else 0))
            parts = [dict(base, amt=total - gst), dict(gst_line, amt=gst)]
        else:
            parts = [dict(base, amt=total)]
    else:
        parts = [dict(t, amt=t["share"] * total) for t in hist_by_ref[row["template"]]]
    return [p for p in _round_lines(parts, round(total, 2)) if p["amt"]]   # history can carry 0.00 lines


def post(key, month, commit=False):
    from xoro_webmethods import WebMethodClient
    acct = ca.ACCOUNTS[key]
    folder = ca.get_month_folder(key, month)
    rows = list(csv.DictReader(open(os.path.join(folder, "coding.csv"))))
    todo = [r for r in rows if r["account"].strip().lower() != "skip"]
    blank = [r for r in todo if not r["account"].strip() and not r["template"]]
    if blank:
        sys.exit("uncoded lines in coding.csv: " + "; ".join(r["description"] for r in blank))

    card_gl = acct["option"].split(" ")[0]
    w = WebMethodClient.from_config()
    accts = w.call("AccountingWebMethods", "getAllAccountsForApi")
    accts = json.loads(accts) if isinstance(accts, str) else accts
    accts = accts.get("Data", accts) if isinstance(accts, dict) else accts
    accounts = {a["AccountNumber"]: a for a in accts}
    card = accounts[card_gl]

    x = XoroClient()
    dates = sorted(dt.datetime.strptime(r["date"], "%m/%d/%Y").date() for r in todo)
    hist = history(card_gl, dates[-1], client=x)
    hist_by_ref, gst_line = {}, None
    for sigs in hist.values():
        for s in sigs.values():
            hist_by_ref[s["ref"]] = s["tmpl"]
            gst_line = gst_line or next((dict(t) for t in s["tmpl"] if t["gl"] == "2226"), None)
    if gst_line is None:
        g = accounts["2226"]
        gst_line = {"gl": "2226", "id": g["FAccountingId"], "name": g["ExtendedName"], "type": g["FAccountTypeId"],
                    "entity": "", "entity_name": ""}

    # already booked: same date, memo and amount on the card
    existing = {(r["TxnDate"], (r["Memo"] or "").strip(), round(-float(r["Amount"]), 2))
                for r in x.get_gl_transactions(dates[0].isoformat(), dates[-1].isoformat(), account_gl_codes=card_gl)
                if r.get("TxnTypeName") == "Journal Entry" and r["GLCode"] == card_gl}

    posted, skipped = [], []
    for r in todo:
        total = round(float(r["amount"]), 2)
        if (r["date"], r["description"].strip(), total) in existing:
            skipped.append(r["description"])
            continue
        parts = build_parts(r, hist_by_ref, accounts, gst_line)
        print("%s  %-40s %9.2f  %s" % (r["date"], r["description"][:40], total,
                                       "  ".join("Dr %s %.2f" % (p["gl"], p["amt"]) for p in parts)))
        if commit:
            res = _save_je(w, r, parts, card, card_gl, total)
            posted.append((r["description"], res.get("Message")))
            if not res.get("Result"):
                sys.exit("stopped: %s -> %s" % (r["description"], res.get("Message")))
    print("%d lines, %d already booked (skipped)%s" % (len(todo), len(skipped), "" if commit else ", dry-run"))
    return posted


def _save_je(w, row, parts, card, card_gl, total):
    sch = w.call("JournalEntryWebMethods", "getJournalEntryObjSchema")
    memo = row["description"]
    h = sch["JournalEntryHeaderObj"]
    dr = round(sum(p["amt"] for p in parts if p["amt"] > 0) + max(-total, 0), 2)
    cur = card["CurrencyId"]   # USD card: every line in USD at Xoro's rate for the day, as the hand-made JEs are
    rate = 1 if cur == 1 else exchange_rate_for(dt.datetime.strptime(row["date"], "%m/%d/%Y").date().isoformat(), "USD")
    h.update(TxnDate=row["date"], Memo=memo, CurrencyId=str(cur), ExchangeRate=rate, TotalDebitAmount=dr, TotalCreditAmount=dr)
    tpl = sch["JournalEntryLineArr"][0]

    def line(n, acc_id, name, typ, gl, amt, entity=""):
        l = copy.deepcopy(tpl)
        l.update(AccountId=acc_id, AccountName=name, AccountTypeId=typ, GLCode=gl,
                 DebitAmount=max(amt, 0), CreditAmount=max(-amt, 0), Amount=amt, OriginalAmount=amt,
                 CurrencyId=cur, ExchangeRate=rate, Memo=memo, LineNumber=n)
        if entity and cur == 1:   # shortcut: USD JEs go without entity (none on the hand-made ones either)
            l.update(EntityAccountId=entity, EntityCurrencyId=1)
        return l

    sch["JournalEntryLineArr"] = [line(i + 1, p["id"], p["name"], p["type"], p["gl"], p["amt"], p["entity"])
                                  for i, p in enumerate(parts)]
    sch["JournalEntryLineArr"].append(line(len(parts) + 1, card["FAccountingId"], card["ExtendedName"],
                                           card["FAccountTypeId"], card_gl, -total))
    sch["SimulatationFlag"] = False
    return w.call("JournalEntryWebMethods", "saveJournalEntry", jeObjJson=json.dumps(sch))


if __name__ == "__main__":
    cmd, key, month = sys.argv[1:4]
    if cmd == "suggest":
        path, rows = suggest(key, month)
        c = collections.Counter(r["source"] for r in rows)
        print(path, dict(c))
    elif cmd == "post":
        post(key, month, commit="--commit" in sys.argv)
