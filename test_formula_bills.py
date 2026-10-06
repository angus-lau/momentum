"""Tests for formula_bills.py — parsing, bill construction and filing (no Xoro calls)."""

import datetime
import unittest
from decimal import Decimal

import xoro_bills as xb
from formula_bills import BalanceError, parse_invoice, to_spec
from test_xoro_bills import REF, past_bill

FORMULA = {"Id": 334, "Name": "Formula Resource Group Ltd.", "CurrencyCode": "CAD"}
HISTORY = [past_bill("08/01/2026", "7520 - Dues, Memberships and Subscriptions", "Standard (BC)")]

# INVOICE.pdf for 81384, verbatim from pdfplumber.
INV_81384 = """INVOICE
Invoice#: 81384
Date: 01 Oct, 2026
Page#: 1
4949 - 52A Street
Order/PO#: October 2026
Delta, BC, V4K 4K1
Shipped By: Electronic
Phone: 604-628-2096
Sold By: Jason
GST Registration #: R126607472
Sold To: Ship To:
St. Moritz Watch St. Moritz Watch
1140 West 7th Avenue 1140 West 7th Avenue
Vancouver, B.C. V6H 1B4 Vancouver, B.C. V6H 1B4
Qty. Description Tax Unit Price Amount
9 Microsoft 365 Business Basic 1 Month Subscription, 1 Month Commit GP 11.40 102.60
7 Microsoft 365 Business Standard 1 Month Subscription, 1 Month Commit GP 22.80 159.60
Subtotal: 262.20
GP - GST 5%, PST 7%
GST 13.11
PST 18.35
If products are not in good working condition within the warranty period, Formula Networks reserves
the right to either: a.) Repair the defective parts or b.) Replace the products. The above products Total Amount 293.66
remain the property of Formula networks until paid for. No cash refunds. 15% restocking charge on
goods accepted for return. Software and media may not be returned if opened.
RECEIVED BY:"""

# A labour invoice (81031): GST only, a decimal quantity, a wrapped description.
INV_81031 = """INVOICE
Invoice#: 81031
Date: 20 Mar, 2026
Qty. Description Tax Unit Price Amount
0.5 Hourly labour charge, 20-Mar-2026 G 110.00 55.00
Assist Simon, convert Microsoft 365 user to shared mailbox and remove
license, add new user and configure email on Microsoft 365
Subtotal: 55.00
G - GST 5%
GST 2.75
If products are not in good working condition Total Amount 57.75
RECEIVED BY:"""


class ParseInvoice(unittest.TestCase):
    def test_header_and_totals(self):
        inv = parse_invoice(INV_81384)
        self.assertEqual(inv["number"], "81384")
        self.assertEqual(inv["date"], datetime.date(2026, 10, 1))
        self.assertEqual(inv["subtotal"], Decimal("262.20"))
        self.assertEqual(inv["gst"], Decimal("13.11"))
        self.assertEqual(inv["pst"], Decimal("18.35"))
        self.assertEqual(inv["total"], Decimal("293.66"))

    def test_line_items_with_their_tax_letter(self):
        lines = parse_invoice(INV_81384)["lines"]
        self.assertEqual([(l["tax"], l["amount"]) for l in lines],
                         [("GP", Decimal("102.60")), ("GP", Decimal("159.60"))])
        self.assertTrue(lines[0]["description"].startswith("Microsoft 365 Business Basic"))

    def test_gst_only_invoice_has_no_pst(self):
        inv = parse_invoice(INV_81031)
        self.assertEqual(inv["pst"], Decimal(0))
        self.assertEqual(inv["lines"], [{"description": "Hourly labour charge, 20-Mar-2026",
                                         "tax": "G", "amount": Decimal("55.00")}])
        self.assertEqual(inv["total"], Decimal("57.75"))


class ToSpec(unittest.TestCase):
    def test_subscription_invoice(self):
        spec = to_spec(parse_invoice(INV_81384), "INVOICE.pdf")
        self.assertEqual((spec["vendor"], spec["folder"], spec["invoice_number"], spec["date"]),
                         ("Formula Resource Group Ltd.", "Formula Resources Group", "81384", "2026-10-01"))
        self.assertEqual([(l["account"], l["tax_code"], l["amount"]) for l in spec["lines"]],
                         [("7520", "Standard (BC)", 262.20)])
        self.assertEqual(spec["taxes"], {"GST": 13.11, "PST": 18.35})
        self.assertEqual((spec["subtotal"], spec["total"]), (262.20, 293.66))

    def test_labour_goes_to_professional_fees_gst_only(self):
        spec = to_spec(parse_invoice(INV_81031), "x.pdf")
        self.assertEqual([(l["account"], l["tax_code"]) for l in spec["lines"]], [("7660", "GST Only")])
        self.assertEqual(spec["taxes"], {"GST": 2.75})

    def test_mixed_invoice_gets_one_line_per_tax_letter(self):
        inv = parse_invoice(INV_81384)
        inv["lines"].append({"description": "labour", "tax": "G", "amount": Decimal("55.00")})
        lines = to_spec(inv, "x.pdf")["lines"]
        self.assertEqual([(l["account"], l["amount"]) for l in lines], [("7520", 262.20), ("7660", 55.00)])

    def test_account_override_applies_to_every_line(self):
        spec = to_spec(parse_invoice(INV_81384), "x.pdf", account="7620")
        self.assertEqual([l["account"] for l in spec["lines"]], ["7620"])

    def test_refuses_an_unknown_tax_letter_or_account(self):
        inv = parse_invoice(INV_81384)
        with self.assertRaises(BalanceError):
            to_spec(inv, "x.pdf", account="1234")
        inv["lines"][0]["tax"] = "E"
        with self.assertRaises(BalanceError):
            to_spec(inv, "x.pdf")


class ThroughTheEngine(unittest.TestCase):
    def build(self, inv):
        return xb.build_bill(to_spec(inv, "x.pdf"), FORMULA, xb.vendor_defaults(HISTORY), REF)

    def test_matches_the_bill_posted_by_hand(self):
        bill, _ = self.build(parse_invoice(INV_81384))
        h = bill["billHeader"]
        self.assertEqual((h["VendorId"], h["VendorBillNumber"], h["DueDate"]), ("334", "81384", "10/31/2026"))
        [line] = bill["billExpenseLineArr"]
        self.assertEqual((line["AccountId"], line["Amount"], line["TaxCodeId"]), ("ID7520", "262.20", "3"))
        self.assertAlmostEqual(line["TaxAmtNonCl"], 18.354)

    def test_refuses_when_the_total_does_not_match(self):
        inv = parse_invoice(INV_81384)
        inv["total"] = Decimal("300.00")
        with self.assertRaises(xb.BillError):
            self.build(inv)

    def test_refuses_when_the_pdf_pst_disagrees_with_seven_percent(self):
        inv = parse_invoice(INV_81384)
        inv["pst"], inv["total"] = Decimal("10.00"), Decimal("285.31")
        with self.assertRaises(xb.BillError):
            self.build(inv)

    def test_files_under_the_fiscal_year(self):
        spec = to_spec(parse_invoice(INV_81031), "x.pdf")
        self.assertEqual(xb.filed_name(spec), "26 03 INV#81031 57.75.pdf")
        self.assertEqual(xb.fiscal_year(parse_invoice(INV_81384)["date"]), 2027)


if __name__ == "__main__":
    unittest.main()
