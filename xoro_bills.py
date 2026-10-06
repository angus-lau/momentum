"""
General expense-bill engine: a *bill spec* (what the invoice says) -> a Xoro Vendor
Bill, checked against the invoice, posted, GL-verified and filed.

A bill spec is a dict (``bills.py`` reads them from JSON):

    {"pdf": "~/Downloads/INVOICE.pdf",
     "vendor": "Automation One",                 # exact Xoro vendor name
     "invoice_number": "AR487362", "date": "2026-09-29",
     "lines": [{"description": "Copier overage", "amount": 10.86}],
     "taxes": {"GST": 0.54, "PST": 0.76},        # as printed on the invoice
     "subtotal": 10.86, "total": 12.16}

Optional: ``currency`` (default: the vendor's), per-line ``account`` ("7640") and
``tax_code`` (Xoro name, e.g. "Standard (BC)"; ``null`` = untaxed), ``terms``
("NET 30"), ``due_date``, ``exchange_rate``, ``folder`` (vendor folder name under
Vendor Invoices - Trade). Anything left out comes from the vendor's own bill
history: the most common account + tax code, the latest bill's terms, store and AP
account. A vendor with no history must give account, tax code and terms.

Tax comes from Xoro's own tax tables, not hardcoded rates: each purchase tax item
on the line's code is computed, and non-collectable items (BC PST) are sent as the
line's ``TaxAmtNonCl``. The Xoro UI computes that client-side and the server does
not — leave it out and the GL books the expense net and the PST vanishes.

Nothing is posted unless the lines reproduce the invoice's subtotal, each tax (by
name: GST, PST, HST, QST) and the total to the cent, and the invoice number isn't
already on a bill for that vendor. Vendors are never created.
"""

import os
import re
import datetime
import difflib
import unicodedata
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP

TRADE_DIR = ("/Users/angus/Library/CloudStorage/OneDrive-St.MoritzWatch/Accounting Docs/"
             "Vendor Invoices - Trade")
HOME_CURRENCY = "CAD"
DEFAULT_STORE = (10001, "CA")
CENT = Decimal("0.01")
RATE_LOOKBACK_DAYS = 5          # weekend/holiday invoices take the last day with postings


class BillError(Exception):
    """The spec can't be turned into a bill that matches the invoice."""


def money(v):
    return Decimal(str(v)).quantize(Decimal("0.0001"))


def cents(v):
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_date(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d").date()


# ---------- reference data (getDataForBill) ----------

class Reference:
    """Lookups over BillWebMethods.getDataForBill."""

    def __init__(self, data):
        self.data = data

    def account(self, number):
        hits = [a for a in self.data["ExpenseAccountList"] if a["Name"].startswith("%s -" % number)]
        if len(hits) != 1:
            raise BillError("expense account %s: %d matches" % (number, len(hits)))
        return {"Id": hits[0]["FAccountingId"], "Name": hits[0]["Name"]}

    def tax_code_id(self, name):
        hits = [t["Id"] for t in self.data["TaxCodeList"] if t["Name"] == name]
        if not hits:
            raise BillError("no tax code named %r" % name)
        return hits[0]

    def tax_items(self, tax_code_id):
        """The purchase tax items under a code, e.g. GST 5% + PST 7% for Standard (BC)."""
        items = [r for r in self.data["TaxRateViewList"]
                 if r["TaxCodeId"] == int(tax_code_id) and r["TaxType"] == "PURCHASE"]
        for r in items:
            if not r["TaxItemIsPercentage"]:
                raise BillError("tax item %s is a flat amount; not supported" % r["TaxItemName"])
        return items

    def terms(self, name=None, term_id=None):
        for t in self.data["PaymentTermList"]:
            if t["Name"] == name or (term_id is not None and str(t["Id"]) == str(term_id)):
                return t
        raise BillError("no payment term %r" % (name or term_id))

    def currency(self, code):
        for c in self.data["CurrencyList"]:
            if c["Code"] == code:
                return c
        raise BillError("no currency %s" % code)

    def trade_payable(self, code):
        """``Accounts Payable - Trade (<ccy>)``."""
        for a in self.data["AccountPayableList"]:
            if "Accounts Payable - Trade (%s)" % code in a["Name"]:
                return a["FAccountingId"]
        raise BillError("no Accounts Payable - Trade (%s) account" % code)


# ---------- tax ----------

def tax_group(item_name):
    """'GST Purchase 5%' -> 'GST', 'PST Purchase (BC) 7%' -> 'PST'."""
    return item_name.split()[0].upper()


def line_tax(amount, items):
    """[(item, tax amount)] plus the non-claimable part, unrounded as Xoro holds it."""
    taxes = [(r, money(amount) * money(r["TaxItemRateFactor"])) for r in items]
    non_claimable = sum((t for r, t in taxes if not r["IsTaxCollectable"]), Decimal(0))
    return taxes, non_claimable


# ---------- defaults from the vendor's history ----------

def _bill_date(h):
    return datetime.datetime.strptime(h["BillDate"], "%m/%d/%Y").date()


def vendor_defaults(bills):
    """Most common account + tax code, and the latest bill's terms/store/AP/currency."""
    bills = sorted(bills, key=lambda b: _bill_date(b["billHeader"]))
    pairs = Counter((l["AccountName"].split(" -")[0], l.get("TaxCodeName"))
                    for b in bills for l in b.get("billExpenseLineArr") or [])
    if not bills:
        return {}
    h = bills[-1]["billHeader"]
    out = {"payment_term_id": h.get("PaymentTermId"), "store": (h["StoreId"], h["StoreName"]),
           "currency": h.get("CurrencyCode"), "ap_account_id": h.get("AccountPayableId")}
    if pairs:
        (account, tax_code), _ = pairs.most_common(1)[0]
        out.update(account=account, tax_code=tax_code)
    return out


# ---------- the bill ----------

def build_bill(spec, vendor, defaults, ref, exchange_rate=1):
    """(billJsonObj, summary) — raises BillError unless it reproduces the invoice."""
    currency = spec.get("currency") or vendor["CurrencyCode"]
    cur = ref.currency(currency)
    date = parse_date(spec["date"])

    if "terms" in spec:
        term = ref.terms(name=spec["terms"])
    elif defaults.get("payment_term_id"):
        term = ref.terms(term_id=defaults["payment_term_id"])
    else:
        raise BillError("%s has no bill history — give \"terms\"" % vendor["Name"])
    if spec.get("due_date"):
        due = parse_date(spec["due_date"])
    else:
        due = date + datetime.timedelta(days=term.get("NetDays") or 0)

    if defaults.get("currency") == currency and defaults.get("ap_account_id"):
        ap = defaults["ap_account_id"]
    else:
        ap = ref.trade_payable(currency)
    store_id, store_name = defaults.get("store") or DEFAULT_STORE

    lines, subtotal, groups, total_tax = [], Decimal(0), {}, Decimal(0)
    for l in spec["lines"]:
        number = l.get("account") or defaults.get("account")
        if not number:
            raise BillError("%s has no bill history — give each line an \"account\"" % vendor["Name"])
        if "tax_code" in l:
            code_name = l["tax_code"]
        elif "account" in defaults:
            code_name = defaults.get("tax_code")
        else:
            raise BillError("%s has no bill history — give each line a \"tax_code\"" % vendor["Name"])
        account = ref.account(number)
        amount = money(l["amount"])
        line = {"AccountId": account["Id"], "AccountName": account["Name"],
                "Amount": "%.2f" % amount, "TaxCodeId": "", "Memo": l.get("description", "")[:200],
                "ProjectClassId": "", "EntityTypeId": "", "EntityId": "", "EntityName": "",
                "EntityCurrencyId": 0, "IntAccntId": "", "StoreId": str(store_id),
                "StoreName": store_name, "LineSeq": len(lines) + 1}
        if code_name:
            code_id = ref.tax_code_id(code_name)
            taxes, non_cl = line_tax(amount, ref.tax_items(code_id))
            tax = sum((t for _, t in taxes), Decimal(0))
            line.update(TaxCodeId=str(code_id), TaxAmt=float(tax), TaxAmtNonCl=float(non_cl),
                        TaxData={"totalAmount": float(tax), "taxItems": [
                            {"itemId": r["TaxItemId"], "itemName": r["TaxItemName"],
                             "itemAmount": float(t), "lineAmount": "%.2f" % amount,
                             "itemRatePerc": float(money(r["TaxItemRateFactor"]) * 100)}
                            for r, t in taxes]})
            for r, t in taxes:
                groups[tax_group(r["TaxItemName"])] = groups.get(tax_group(r["TaxItemName"]), Decimal(0)) + t
            total_tax += tax
        line["_code_name"] = code_name
        lines.append(line)
        subtotal += amount

    check(spec, subtotal, groups)

    d = date.strftime("%m/%d/%Y")
    header = {
        "Id": 0, "BillNumber": None, "TxnId": 0, "TxnNumber": 0, "StoreId": str(store_id),
        "VendorId": str(vendor["Id"]), "TypeId": 20, "AccountPayableId": ap,
        "TxnDate": d, "OldTxnDate": d, "BillDate": d, "DueDate": due.strftime("%m/%d/%Y"),
        "DiscountDate": "", "ExchangeRate": exchange_rate, "CurrencyId": cur["Id"],
        "PaymentTermId": str(term["Id"]), "PaymentTermName": None, "RefNo": "",
        "TotalAmt": float(money(spec["total"])), "TotalTaxAmt": float(total_tax),
        "OldTotalAmt": 0, "ReconcileFlag": False, "Memo": "",
        "BillFromCompanyName": vendor["Name"], "BillFromFirstName": "", "BillFromLastName": "",
        "BillFromAddress2": "", "BillFromPhoneNumber": "", "BillFromEmail": "",
        "StoreName": store_name, "VendorBillNumber": spec["invoice_number"],
        "ProjectClassId": "", "BuyerId": "", "TotalCBM": 0,
        "UndoReconcilation": False, "IsConvertToBill": False, "RestrictVoidExpenseBill": False,
        "IsMultiReconcilation": False, "UndoReconcileLinkedExpenseBills": False,
        "TaxServiceHashCode": 0, "EntityUseCode": None,
    }
    summary = {"currency": currency, "terms": term["Name"], "exchange_rate": exchange_rate,
               "lines": [(l["AccountName"], l["Amount"], l.pop("_code_name"),
                          l.get("TaxAmt", 0), l.get("TaxAmtNonCl", 0)) for l in lines],
               "non_claimable": sum((Decimal(str(l.get("TaxAmtNonCl", 0))) for l in lines), Decimal(0))}
    return {"billHeader": header, "billItemLineArr": [], "billExpenseLineArr": lines}, summary


def check(spec, subtotal, groups):
    """The lines must reproduce the invoice's subtotal, each tax and the total."""
    printed = {k.upper(): money(v) for k, v in (spec.get("taxes") or {}).items()}
    problems = []
    if cents(subtotal) != money(spec["subtotal"]):
        problems.append("subtotal: lines give %s, invoice says %s" % (cents(subtotal), money(spec["subtotal"])))
    for name in sorted(set(groups) | set(printed)):
        ours, theirs = cents(groups.get(name, 0)), printed.get(name, Decimal(0))
        if ours != theirs:
            problems.append("%s: tax codes give %s, invoice says %s" % (name, ours, theirs))
    if money(spec["subtotal"]) + sum(printed.values(), Decimal(0)) != money(spec["total"]):
        problems.append("total: subtotal + taxes = %s, invoice says %s"
                        % (money(spec["subtotal"]) + sum(printed.values(), Decimal(0)), money(spec["total"])))
    if problems:
        raise BillError("; ".join(problems))


def gl_problems(rows, total, expected_expense):
    """After posting: the bill's GL rows must balance, AP must carry the total, and
    the expense side must be net + non-claimable tax (PST actually booked)."""
    problems = []
    amounts = [Decimal(str(r.get("Amount") or 0)) for r in rows]
    if not rows:
        return ["no GL rows found for the bill"]
    if abs(sum(amounts)) > Decimal("0.01"):
        problems.append("GL does not balance (off by %s)" % sum(amounts))
    ap = [Decimal(str(r.get("Amount") or 0)) for r in rows
          if "Accounts Payable" in (r.get("F_AccountingName") or r.get("AccountName") or "")]
    if cents(-sum(ap, Decimal(0))) != cents(total):
        problems.append("AP carries %s, bill total is %s" % (-sum(ap, Decimal(0)), total))
    expense = sum((Decimal(str(r.get("Amount") or 0)) for r in rows
                   if (r.get("GLCode") or "")[:1] in "5678" and (r.get("GLCode") or "") != "6601"),
                  Decimal(0))
    if abs(expense - expected_expense) > Decimal("0.01"):
        problems.append("expense booked %s, expected %s (net + non-claimable tax)" % (expense, expected_expense))
    return problems


# ---------- filing ----------

_SUFFIXES = r"\b(ltd|limited|inc|incorporated|corp|corporation|co|company|llc|llp|the)\b\.?"


def _norm(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"\(.*?\)", " ", name.lower())
    name = re.sub(_SUFFIXES, " ", name)
    return " ".join(re.findall(r"[a-z0-9&]+", name))


def match_vendor_folder(vendor_name, folder_names):
    """The trade folder for a vendor, by fuzzy name; None when absent or ambiguous."""
    target = _norm(vendor_name)
    scored = sorted(((difflib.SequenceMatcher(None, target, _norm(f)).ratio(), f)
                     for f in folder_names), reverse=True)
    if not scored or scored[0][0] < 0.85:
        return None
    if len(scored) > 1 and scored[1][0] > scored[0][0] - 0.05:
        return None
    return scored[0][1]


def fiscal_year(date):
    """FY ends July 31: Aug–Dec belong to the next year."""
    return date.year + 1 if date.month >= 8 else date.year


def year_folder(existing, fy):
    """Name the FY folder the way this vendor's folder already does (YE 2027 / YE2027 / FY 2027)."""
    styles = []
    for name in existing:
        m = re.fullmatch(r"(YE|FY)( ?)(\d{4})", name.strip())
        if m:
            styles.append((int(m.group(3)), m.group(1), m.group(2)))
    if not styles:
        return "YE %d" % fy
    _, prefix, space = max(styles)
    return "%s%s%d" % (prefix, space, fy)


def filed_name(spec):
    d = parse_date(spec["date"])
    number = re.sub(r'[\\/:*?"<>|]', "-", spec["invoice_number"])
    return "%s INV#%s %.2f.pdf" % (d.strftime("%y %m"), number, money(spec["total"]))


def filed_path(spec, vendor_name, trade_dir=TRADE_DIR):
    folder = spec.get("folder")
    if not folder:
        folder = match_vendor_folder(vendor_name, [f for f in os.listdir(trade_dir)
                                                   if os.path.isdir(os.path.join(trade_dir, f))])
        if not folder:
            raise BillError("no single trade folder matches %r — give \"folder\"" % vendor_name)
    vendor_dir = os.path.join(trade_dir, folder)
    existing = os.listdir(vendor_dir) if os.path.isdir(vendor_dir) else []
    fy_dir = year_folder(existing, fiscal_year(parse_date(spec["date"])))
    return os.path.join(vendor_dir, fy_dir, filed_name(spec))


# ---------- Xoro I/O ----------

class Xoro:
    """The live calls the engine needs, kept apart from the pure logic above."""

    def __init__(self):
        from xoro_api import XoroClient
        from xoro_webmethods import WebMethodClient
        self.rest = XoroClient.from_config()
        self.wm = WebMethodClient.from_config()
        self._ref = None

    def reference(self):
        if self._ref is None:
            self._ref = Reference(self.wm.call("BillWebMethods", "getDataForBill")["Data"])
        return self._ref

    def vendor(self, name):
        found = self.wm.call("Common", "getGeneralEntityListByKeyword", keyword=name).get("Data") or []
        vendors = [v for v in found if v["EntityTypeName"] == "Vendor"]
        exact = [v for v in vendors if v["Name"].strip().lower() == name.strip().lower()]
        if len(exact) == 1:
            return exact[0]
        raise BillError("vendor %r not found exactly in Xoro (candidates: %s) — create it in Xoro "
                        "or use its exact name" % (name, ", ".join(v["Name"] for v in vendors) or "none"))

    def vendor_bills(self, vendor):
        return [b for b in self.rest.get_bills(vendor_name=vendor["Name"])
                if str(b["billHeader"].get("VendorId")) == str(vendor["Id"])]

    def exchange_rate(self, date, currency):
        from xoro_api import exchange_rate_for, AmbiguousRate
        if currency == HOME_CURRENCY:
            return 1, date
        last = None
        for back in range(RATE_LOOKBACK_DAYS + 1):
            day = date - datetime.timedelta(days=back)
            try:
                return exchange_rate_for(day.isoformat(), currency, client=self.rest), day
            except AmbiguousRate as e:
                if not str(e).startswith("no "):      # a tie is real ambiguity: don't guess
                    raise BillError("%s on %s — give \"exchange_rate\"" % (e, day))
                last = e
        raise BillError("no %s rate within %d days of %s (%s) — give \"exchange_rate\""
                        % (currency, RATE_LOOKBACK_DAYS, date, last))

    def create(self, bill):
        import json
        r = self.wm.call("BillWebMethods", "createNewBill", billJsonObj=json.dumps(bill))
        if not r.get("Result"):
            raise BillError("createNewBill failed: %s" % r.get("Message"))
        return r["Data"]["billHeader"]["BillNumber"]

    def gl_rows(self, bill_number, date):
        d = date.isoformat()
        return [r for r in self.rest.get_gl_transactions(d, d) if bill_number in str(r)]
