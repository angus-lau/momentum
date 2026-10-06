"""Tests for xoro_bills.py — the general bill engine (no Xoro calls)."""

import datetime
import unittest
from decimal import Decimal

import xoro_bills as xb


def tax_row(code, item_id, name, rate, collectable, ttype="PURCHASE"):
    return {"TaxCodeId": code, "TaxItemId": item_id, "TaxItemName": name, "TaxType": ttype,
            "TaxItemRateFactor": rate, "IsTaxCollectable": collectable, "TaxItemIsPercentage": True}


# Shapes and values as returned by BillWebMethods.getDataForBill.
REF = xb.Reference({
    "ExpenseAccountList": [
        {"FAccountingId": "ID7520", "Name": "7520 - Dues, Memberships and Subscriptions"},
        {"FAccountingId": "ID7640", "Name": "7640 - Printing Expenses - Administrative"},
        {"FAccountingId": "ID7000", "Name": "7000 - Warranty Expense"},
        {"FAccountingId": "ID7660", "Name": "7660 - Professional Fees"},
    ],
    "TaxCodeList": [{"Id": 2, "Name": "GST Only"}, {"Id": 3, "Name": "Standard (BC)"}],
    "TaxRateViewList": [
        tax_row(3, 110, "GST Purchase 5%", 0.05, True),
        tax_row(3, 100, "GST Sale 5%", 0.05, True, "SALE"),
        tax_row(3, 130, "PST Purchase (BC) 7%", 0.07, False),
        tax_row(3, 120, "PST Sale (BC) 7%", 0.07, True, "SALE"),
        tax_row(2, 110, "GST Purchase 5%", 0.05, True),
    ],
    "PaymentTermList": [{"Id": 1286, "Name": "NET 30", "NetDays": 30},
                        {"Id": 1285, "Name": "Due on receipt", "NetDays": 0}],
    "CurrencyList": [{"Id": 1, "Code": "CAD"}, {"Id": 1001, "Code": "USD"}],
    "AccountPayableList": [
        {"FAccountingId": "AP-CAD", "Name": "2100 - Accounts Payable - Trade (CAD)"},
        {"FAccountingId": "AP-USD", "Name": "2101 - Accounts Payable - Trade (USD)"},
        {"FAccountingId": "AP-INV-USD", "Name": "2141 - Accounts Payable - Inventory (USD)"},
    ],
})

AUTOMATION_ONE = {"Id": 293, "Name": "Automation One", "CurrencyCode": "CAD"}
LOWEN = {"Id": 400, "Name": "Lowen Watch Group", "CurrencyCode": "USD"}


def past_bill(date, account, tax_code, term=1286, ap="AP-CAD", ccy="CAD", number="X"):
    return {"billHeader": {"BillDate": date, "PaymentTermId": term, "StoreId": 10001,
                           "StoreName": "CA", "CurrencyCode": ccy, "AccountPayableId": ap,
                           "VendorBillNumber": number},
            "billExpenseLineArr": [{"AccountName": account, "TaxCodeName": tax_code}]}


A1_HISTORY = [
    past_bill("07/30/2026", "7640 - Printing Expenses - Administrative", "Standard (BC)"),
    past_bill("06/12/2025", "7640 - Printing Expenses - Administrative", "Standard (BC)"),
    past_bill("08/10/2026", "7640 - Printing Expenses - Administrative", None),
]

# AR487362, as posted by hand on 2026-10-05.
A1_SPEC = {"pdf": "x.pdf", "vendor": "Automation One", "invoice_number": "AR487362",
           "date": "2026-09-29", "lines": [{"description": "Copier overage", "amount": 10.86}],
           "taxes": {"GST": 0.54, "PST": 0.76}, "subtotal": 10.86, "total": 12.16}


def spec(**over):
    s = dict(A1_SPEC, lines=[dict(l) for l in A1_SPEC["lines"]])
    s.update(over)
    return s


class VendorDefaults(unittest.TestCase):
    def test_most_common_account_and_tax_code(self):
        d = xb.vendor_defaults(A1_HISTORY)
        self.assertEqual((d["account"], d["tax_code"]), ("7640", "Standard (BC)"))

    def test_terms_store_and_ap_from_the_latest_bill(self):
        hist = A1_HISTORY + [past_bill("09/01/2026", "7640 - Printing Expenses - Administrative",
                                       "Standard (BC)", term=1285)]
        d = xb.vendor_defaults(hist)
        self.assertEqual(d["payment_term_id"], 1285)
        self.assertEqual(d["store"], (10001, "CA"))
        self.assertEqual(d["ap_account_id"], "AP-CAD")

    def test_no_history_gives_nothing(self):
        self.assertEqual(xb.vendor_defaults([]), {})


class BuildBill(unittest.TestCase):
    def build(self, s=None, vendor=AUTOMATION_ONE, history=A1_HISTORY, rate=1):
        return xb.build_bill(s or spec(), vendor, xb.vendor_defaults(history), REF, rate)

    def test_reproduces_the_hand_posted_automation_one_bill(self):
        bill, _ = self.build()
        h = bill["billHeader"]
        self.assertEqual((h["VendorId"], h["VendorBillNumber"]), ("293", "AR487362"))
        self.assertEqual((h["BillDate"], h["DueDate"]), ("09/29/2026", "10/29/2026"))
        self.assertEqual((h["PaymentTermId"], h["AccountPayableId"], h["CurrencyId"]), ("1286", "AP-CAD", 1))
        self.assertEqual(h["TotalAmt"], 12.16)
        [line] = bill["billExpenseLineArr"]
        self.assertEqual((line["AccountId"], line["Amount"], line["TaxCodeId"]), ("ID7640", "10.86", "3"))
        self.assertAlmostEqual(line["TaxAmt"], 1.3032)

    def test_pst_is_sent_as_non_claimable(self):
        bill, s = self.build()
        [line] = bill["billExpenseLineArr"]
        self.assertAlmostEqual(line["TaxAmtNonCl"], 0.7602)
        self.assertEqual([i["itemId"] for i in line["TaxData"]["taxItems"]], [110, 130])
        self.assertEqual(s["non_claimable"], Decimal("0.7602"))

    def test_sale_tax_items_are_ignored(self):
        bill, _ = self.build()
        ids = [i["itemId"] for i in bill["billExpenseLineArr"][0]["TaxData"]["taxItems"]]
        self.assertNotIn(100, ids)
        self.assertNotIn(120, ids)

    def test_spec_overrides_account_tax_code_and_terms(self):
        s = spec(terms="Due on receipt", taxes={"GST": 0.54}, total=11.40)
        s["lines"][0].update(account="7660", tax_code="GST Only")
        bill, _ = self.build(s)
        line = bill["billExpenseLineArr"][0]
        self.assertEqual((line["AccountName"][:4], line["TaxCodeId"], line["TaxAmtNonCl"]), ("7660", "2", 0))
        self.assertEqual(bill["billHeader"]["DueDate"], "09/29/2026")

    def test_null_tax_code_is_untaxed(self):
        s = spec(taxes={}, total=10.86)
        s["lines"][0]["tax_code"] = None
        line = self.build(s)[0]["billExpenseLineArr"][0]
        self.assertEqual(line["TaxCodeId"], "")
        self.assertNotIn("TaxData", line)

    def test_usd_vendor_uses_trade_usd_and_the_rate(self):
        hist = [past_bill("08/18/2026", "7000 - Warranty Expense", None, ap="AP-USD", ccy="USD")]
        s = spec(vendor="Lowen Watch Group", taxes={}, subtotal=595.98, total=595.98,
                 lines=[{"description": "repairs", "amount": 595.98}])
        bill, _ = self.build(s, vendor=LOWEN, history=hist, rate=1.37725)
        h = bill["billHeader"]
        self.assertEqual((h["CurrencyId"], h["AccountPayableId"], h["ExchangeRate"]), (1001, "AP-USD", 1.37725))

    def test_new_currency_for_a_vendor_falls_back_to_trade_ap(self):
        hist = [past_bill("08/18/2026", "7000 - Warranty Expense", None, ap="AP-INV-USD", ccy="USD")]
        s = spec(currency="CAD", taxes={}, subtotal=10, total=10, lines=[{"amount": 10}])
        bill, _ = self.build(s, vendor=LOWEN, history=hist)
        self.assertEqual(bill["billHeader"]["AccountPayableId"], "AP-CAD")

    def test_vendor_without_history_must_spell_everything_out(self):
        with self.assertRaisesRegex(xb.BillError, "terms"):
            self.build(history=[])
        s = spec(terms="NET 30")
        with self.assertRaisesRegex(xb.BillError, "account"):
            self.build(s, history=[])
        s["lines"][0]["account"] = "7640"
        with self.assertRaisesRegex(xb.BillError, "tax_code"):
            self.build(s, history=[])
        s["lines"][0]["tax_code"] = "Standard (BC)"
        self.build(s, history=[])

    def test_one_line_per_spec_line_numbered_from_one(self):
        s = spec(subtotal=20.86, taxes={"GST": 1.04, "PST": 1.46}, total=23.36,
                 lines=[{"amount": 10.86}, {"amount": 10.00}])
        lines = self.build(s)[0]["billExpenseLineArr"]
        self.assertEqual([(l["Amount"], l["LineSeq"]) for l in lines], [("10.86", 1), ("10.00", 2)])


class Checks(unittest.TestCase):
    def build(self, s):
        return xb.build_bill(s, AUTOMATION_ONE, xb.vendor_defaults(A1_HISTORY), REF)

    def test_refuses_a_wrong_subtotal(self):
        with self.assertRaisesRegex(xb.BillError, "subtotal"):
            self.build(spec(subtotal=11.00, total=12.30))

    def test_refuses_when_a_printed_tax_disagrees(self):
        with self.assertRaisesRegex(xb.BillError, "PST"):
            self.build(spec(taxes={"GST": 0.54, "PST": 0.70}, total=12.10))

    def test_refuses_a_tax_the_code_does_not_produce(self):
        s = spec(taxes={"GST": 0.54}, total=11.40)
        s["lines"][0]["tax_code"] = None
        with self.assertRaisesRegex(xb.BillError, "GST"):
            self.build(s)

    def test_refuses_when_the_total_does_not_add_up(self):
        with self.assertRaisesRegex(xb.BillError, "total"):
            self.build(spec(total=12.20))

    def test_unknown_account_or_tax_code(self):
        s = spec()
        s["lines"][0]["account"] = "9999"
        with self.assertRaises(xb.BillError):
            self.build(s)
        s["lines"][0].update(account="7640", tax_code="Nope")
        with self.assertRaises(xb.BillError):
            self.build(s)


class GLCheck(unittest.TestCase):
    ROWS = [{"GLCode": "6601", "F_AccountingName": "6601 - Penny Rounding Adjustments - CAD", "Amount": 0.0},
            {"GLCode": "2226", "F_AccountingName": "2226 - GST/HST Payable", "Amount": 0.543},
            {"GLCode": "2100", "F_AccountingName": "2100 - Accounts Payable - Trade (CAD)", "Amount": -12.16},
            {"GLCode": "7640", "F_AccountingName": "7640 - Printing Expenses", "Amount": 11.6202}]

    def test_the_real_ca_b002030_posting_passes(self):
        self.assertEqual(xb.gl_problems(self.ROWS, Decimal("12.16"), Decimal("11.6202")), [])

    def test_catches_pst_left_off_the_expense(self):
        rows = [dict(r) for r in self.ROWS]
        rows[3]["Amount"] = 10.86
        problems = xb.gl_problems(rows, Decimal("12.16"), Decimal("11.6202"))
        self.assertTrue(any("balance" in p for p in problems))
        self.assertTrue(any("expense" in p for p in problems))

    def test_no_rows(self):
        self.assertEqual(xb.gl_problems([], Decimal(1), Decimal(1)), ["no GL rows found for the bill"])


class Filing(unittest.TestCase):
    FOLDERS = ["Automation One", "Formula Resources Group", "Automation Data Processing - Tax Filing",
               "Avalara Europe Ltd (UK EU VAT)", "Ceridian", "Lowen Watch Group"]

    def test_matches_despite_suffixes_and_plurals(self):
        self.assertEqual(xb.match_vendor_folder("Formula Resource Group Ltd.", self.FOLDERS),
                         "Formula Resources Group")
        self.assertEqual(xb.match_vendor_folder("Avalara Europe Ltd", self.FOLDERS),
                         "Avalara Europe Ltd (UK EU VAT)")
        self.assertEqual(xb.match_vendor_folder("Automation One", self.FOLDERS), "Automation One")

    def test_accents_are_ignored(self):
        self.assertEqual(xb.match_vendor_folder("Lowen Watch Group", ["Löwen Watch Group", "Bell"]),
                         "Löwen Watch Group")

    def test_no_match_returns_none(self):
        self.assertIsNone(xb.match_vendor_folder("Bell Canada Mobility", self.FOLDERS))

    def test_ambiguous_match_returns_none(self):
        self.assertIsNone(xb.match_vendor_folder("Acme", ["Acme Ltd", "Acme Inc"]))

    def test_fiscal_year(self):
        self.assertEqual(xb.fiscal_year(datetime.date(2026, 7, 31)), 2026)
        self.assertEqual(xb.fiscal_year(datetime.date(2026, 8, 1)), 2027)

    def test_year_folder_follows_the_vendors_style(self):
        self.assertEqual(xb.year_folder(["YE 2025", "YE 2026", "x.xlsx"], 2027), "YE 2027")
        self.assertEqual(xb.year_folder(["YE2026"], 2027), "YE2027")
        self.assertEqual(xb.year_folder(["FY 2026"], 2027), "FY 2027")
        self.assertEqual(xb.year_folder([], 2027), "YE 2027")

    def test_file_name(self):
        self.assertEqual(xb.filed_name(A1_SPEC), "26 09 INV#AR487362 12.16.pdf")
        self.assertEqual(xb.filed_name(spec(invoice_number="A/B")), "26 09 INV#A-B 12.16.pdf")


if __name__ == "__main__":
    unittest.main()
