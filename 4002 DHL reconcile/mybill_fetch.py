#!/usr/bin/env python3
"""
Fetch the month's DHL invoice PDFs from MyBill into the Amex Sofia 4002 folder.

Usage:
    python3 mybill_fetch.py "/path/to/Amex Sofia 4002/26 08"            # download
    python3 mybill_fetch.py "/path/to/Amex Sofia 4002/26 08" --dry-run  # match only

Reads the folder's ``activity.csv`` (the Amex export), takes every DHL charge on it,
matches each one to a MyBill invoice, and saves the invoice PDF next to the CSV as
``<amount> <invoice#>.pdf`` — the naming ``dhl_reconcile.py`` already expects. Run
``run.sh`` afterwards as usual.

Two safeguards per charge, both must hold:
  1. amount equals the invoice total (to the cent)
  2. the charge date is within DATE_WINDOW of the invoice's due date — DHL's autopay
     hits the card on (or a day or two after) the due date: 7 days after invoice date
     for duty/customs invoices, 14 for shipping invoices
Anything unmatched or ambiguous stops the run before any download.

MyBill has no billing API, so this drives the logged-in "chrome-debug" Chrome over
CDP (same persistent profile Xoro uses). Log in to https://mybill.dhl.com there once;
the session is reused. Chrome may be listening on IPv4 or IPv6 depending on whether
the normal Chrome also holds port 9222 — both are tried.

Requires ``websocket-client`` (see requirements.txt).
"""

import sys
import re
import json
import html
import base64
import pathlib
import datetime
import itertools
import urllib.request
from urllib.error import URLError

# convert_activity.py lives one level up; reuse its row parsing + DHL filter.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import convert_activity as ca  # noqa: E402

BANK_CONFIG_KEY = "amex_sofia"
CDP_CANDIDATES = ("http://127.0.0.1:9222", "http://[::1]:9222")
DATE_WINDOW = (-3, 7)  # days: charge date - due date must fall in [lo, hi]
ARCHIVE_PAGES = ("/archive/?per_page=1000", "/dashboard/?per_page=1000")


class MatchError(Exception):
    """A charge could not be matched to exactly one invoice."""


# ---------- dates ----------

_DJANGO_MONTHS = {
    "jan": 1, "feb": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "aug": 8, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_mybill_date(raw):
    """MyBill shows Django's ``N j, Y`` — ``Sept. 2, 2026``, ``July 31, 2026``."""
    m = re.match(r"\s*([A-Za-z]+)\.?\s+(\d{1,2}),\s*(\d{4})", raw or "")
    if not m:
        return None
    month = _DJANGO_MONTHS.get(m.group(1).lower())
    if not month:
        return None
    return datetime.date(int(m.group(3)), month, int(m.group(2)))


def parse_amex_date(raw):
    """Amex export date, ``09 Sep 2026``."""
    return datetime.datetime.strptime(raw.strip(), "%d %b %Y").date()


# ---------- MyBill HTML ----------

_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAGS = re.compile(r"<[^>]+>")


def _cell_text(td):
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", td))).strip()


def parse_invoice_rows(page_html):
    """Invoice rows from the Archive / Dashboard tables.

    Each row carries its document id on the multiselect checkbox (``name="<id>"``).
    Most cells are labelled with ``data-header``; Invoice Type and Total are not,
    but sit directly after the labelled "Invoice No." and "Status" cells, so they're
    picked up by position relative to those. Returns dicts: id, no, type, date,
    due, total.
    """
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.S):
        m = re.search(r'<input[^>]*name="(\d{6,})"', tr)
        if not m:
            continue
        cells = []  # ordered (label or None, text)
        for td in re.finditer(r"<td([^>]*)>(.*?)</td>", tr, re.S):
            hdr = re.search(r'data-header=["\']([^"\']+)', td.group(1))
            cells.append((hdr.group(1).strip().lower() if hdr else None, _cell_text(td.group(2))))
        labelled = {label: text for label, text in cells if label}

        def after(label):
            for i, (lab, _) in enumerate(cells):
                if lab == label and i + 1 < len(cells) and cells[i + 1][0] is None:
                    return cells[i + 1][1]
            return ""

        mt = re.search(r"-?[\d,]+\.\d{2}", after("status"))
        out.append({
            "id": m.group(1),
            "no": labelled.get("invoice no.", labelled.get("invoice no", "")),
            "type": after("invoice no."),
            "date": parse_mybill_date(labelled.get("invoice date", "")),
            "due": parse_mybill_date(labelled.get("due date", "")),
            "total": float(mt.group(0).replace(",", "")) if mt else None,
        })
    return out


# ---------- matching ----------

def match_charges(charges, invoices):
    """Pair each ``(charge_date, amount)`` with exactly one invoice.

    Amount must equal the invoice total; the charge date must land within
    DATE_WINDOW of the invoice's due date (invoice date if MyBill shows no due
    date). An invoice is used at most once. Raises MatchError listing every
    problem rather than stopping at the first, so one run shows all of them.
    """
    by_total = {}
    for inv in invoices:
        if inv["total"] is not None:
            by_total.setdefault(round(inv["total"], 2), []).append(inv)

    lo, hi = DATE_WINDOW
    used = set()
    matches, problems = [], []
    for charge_date, amount in charges:
        amount = round(amount, 2)
        cands = []
        for inv in by_total.get(amount, []):
            anchor = inv["due"] or inv["date"]
            if anchor is None or inv["id"] in used:
                continue
            if lo <= (charge_date - anchor).days <= hi:
                cands.append(inv)
        if len(cands) == 1:
            used.add(cands[0]["id"])
            matches.append({"charge_date": charge_date, "amount": amount, "invoice": cands[0]})
        elif not cands:
            same_amt = [i["no"] for i in by_total.get(amount, [])]
            hint = f" (same amount but wrong date: {', '.join(same_amt)})" if same_amt else ""
            problems.append(f"{charge_date} {amount:.2f}: no invoice matches{hint}")
        else:
            problems.append(f"{charge_date} {amount:.2f}: ambiguous — "
                            + ", ".join(f"{i['no']} due {i['due']}" for i in cands))
    if problems:
        raise MatchError("\n".join(problems))
    return matches


def pdf_name(amount, invoice_no):
    return f"{amount:.2f} {invoice_no}.pdf"


# ---------- CDP (debug Chrome) ----------

def find_cdp():
    """Return the http base of a Chrome that answers DevTools on 9222.

    The normal Chrome can squat on 127.0.0.1:9222 without serving DevTools (404),
    in which case chrome-debug binds [::1]:9222 instead — so try both and keep the
    one that returns a real /json/version.
    """
    for base in CDP_CANDIDATES:
        try:
            with urllib.request.urlopen(f"{base}/json/version", timeout=3) as r:
                info = json.loads(r.read().decode())
            if "webSocketDebuggerUrl" in info:
                return base
        except (URLError, ValueError, OSError):
            continue
    raise SystemExit(
        "No debug Chrome found on port 9222. Start it with the `chrome-debug` alias "
        "and log in to https://mybill.dhl.com, then re-run."
    )


class MyBill:
    """Runs fetch() inside the logged-in MyBill tab so its cookies apply."""

    def __init__(self, base):
        import websocket  # deferred: only needed for the live path
        tabs = json.load(urllib.request.urlopen(f"{base}/json"))
        tab = next((t for t in tabs if t["type"] == "page" and "mybill.dhl.com" in t["url"]), None)
        if tab is None:
            raise SystemExit("No mybill.dhl.com tab open in the debug Chrome — open it and log in.")
        if "/login" in tab["url"]:
            raise SystemExit("MyBill tab is on the login page — log in first, then re-run.")
        self.ws = websocket.create_connection(tab["webSocketDebuggerUrl"], suppress_origin=True)
        self.ids = itertools.count(1)

    def _js(self, expr):
        i = next(self.ids)
        self.ws.send(json.dumps({"id": i, "method": "Runtime.evaluate",
                                 "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == i:
                if "error" in m:
                    raise RuntimeError(m["error"])
                res = m["result"]
                if "exceptionDetails" in res:
                    raise RuntimeError(res["exceptionDetails"].get("text", "JS error"))
                return res["result"].get("value")

    def page(self, path):
        return self._js(f"fetch({json.dumps(path)}).then(r => r.text())")

    def invoices(self):
        seen, out = set(), []
        for path in ARCHIVE_PAGES:
            for inv in parse_invoice_rows(self.page(path)):
                if inv["id"] not in seen:
                    seen.add(inv["id"])
                    out.append(inv)
        if not out:
            raise SystemExit("MyBill returned no invoices — is the session still logged in?")
        return out

    def download_pdf(self, doc_id):
        """MyBill's download_pdf_customer task: request → poll → fetch. Returns bytes."""
        res = self._js("""(async () => {
          const h = await (await fetch('/archive/')).text();
          const csrf = h.match(/name="csrfmiddlewaretoken" value="([^"]+)"/)[1];
          const body = new URLSearchParams({celery_task_name: 'download_pdf_customer',
            document_summary_id: %s, per_page: '20', celery_task_id: '', event_target_id: '',
            celery_store_result_on_page: 'False', csrfmiddlewaretoken: csrf});
          const j = await (await fetch('/task_queue/request_download/', {method: 'POST', body,
            headers: {'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest'}})).json();
          if (!j.task_id) return {error: 'no task_id: ' + JSON.stringify(j)};
          let state = null;
          for (let i = 0; i < 60; i++) {
            const p = await (await fetch('/task_queue/poll_download/' + j.task_id + '/')).json();
            state = p.celery_task_state;
            if (state === 'SUCCESS' || state === 'FAILURE' || state === 'REVOKED') break;
            await new Promise(r => setTimeout(r, 1000));
          }
          if (state !== 'SUCCESS') return {error: 'task state ' + state};
          const f = await fetch('/task_queue/fetch_download/' + j.task_id + '/');
          const ct = f.headers.get('content-type') || '';
          const bytes = new Uint8Array(await f.arrayBuffer());
          let bin = ''; for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
          return {ct, b64: btoa(bin)};
        })()""" % json.dumps(str(doc_id)))
        if "error" in res:
            raise RuntimeError(f"doc {doc_id}: {res['error']}")
        data = base64.b64decode(res["b64"])
        if not data.startswith(b"%PDF"):
            raise RuntimeError(f"doc {doc_id}: not a PDF ({res['ct']}, {len(data)} bytes)")
        return data


# ---------- main ----------

def main(folder, dry_run=False):
    folder = pathlib.Path(folder)
    activity = folder / "activity.csv"
    if not activity.exists():
        raise SystemExit(f"No activity.csv in {folder}")

    charges = [(parse_amex_date(d), a) for d, a in ca.dhl_charges(BANK_CONFIG_KEY, str(activity))]
    print(f"{len(charges)} DHL charges on the statement, total {sum(a for _, a in charges):,.2f}")
    if not charges:
        return

    mb = MyBill(find_cdp())
    invoices = mb.invoices()
    print(f"{len(invoices)} invoices listed in MyBill")

    try:
        matches = match_charges(charges, invoices)
    except MatchError as e:
        print("\nCould not match every charge — nothing downloaded:\n" + str(e), file=sys.stderr)
        sys.exit(2)

    print()
    todo = []
    for m in sorted(matches, key=lambda m: m["charge_date"]):
        inv = m["invoice"]
        name = pdf_name(m["amount"], inv["no"])
        exists = (folder / name).exists()
        print(f"  {m['charge_date']}  {m['amount']:9.2f}  {inv['no']:14} {inv['type']:16} "
              f"inv {inv['date']} due {inv['due']}  {'already here' if exists else ''}")
        if not exists:
            todo.append((name, inv))

    if dry_run:
        print(f"\nDry run — {len(todo)} PDF(s) would be downloaded to {folder}")
        return

    for name, inv in todo:
        data = mb.download_pdf(inv["id"])
        (folder / name).write_bytes(data)
        print(f"  saved {name} ({len(data):,} bytes)")
    print(f"\nDone: {len(todo)} downloaded, {len(matches) - len(todo)} already present, in {folder}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)
    main(args[0], dry_run="--dry-run" in sys.argv)
