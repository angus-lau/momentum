"""Tests for formula_bills.py — parsing, bill construction and filing (no Xoro calls)."""

import datetime
import unittest
from decimal import Decimal

from formula_bills import (
    ACCOUNTS,
    BalanceError,
    build_bill,
    filed_path,
    parse_invoice,
)

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


class BuildBill(unittest.TestCase):
    def test_subscription_bill_matches_the_one_posted_by_hand(self):
        bill = build_bill(parse_invoice(INV_81384))
        h = bill["billHeader"]
        self.assertEqual((h["VendorId"], h["VendorBillNumber"]), ("334", "81384"))
        self.assertEqual((h["BillDate"], h["DueDate"]), ("10/01/2026", "10/31/2026"))
        self.assertEqual(h["TotalAmt"], 293.66)
        [line] = bill["billExpenseLineArr"]
        self.assertEqual(line["AccountName"], ACCOUNTS["7520"]["Name"])
        self.assertEqual((line["Amount"], line["TaxCodeId"]), ("262.20", "3"))
        self.assertAlmostEqual(line["TaxAmt"], 31.464)

    def test_pst_is_carried_as_non_claimable(self):
        # without TaxAmtNonCl Xoro books 7520 net and drops the PST from the GL
        [line] = build_bill(parse_invoice(INV_81384))["billExpenseLineArr"]
        self.assertAlmostEqual(line["TaxAmtNonCl"], 18.354)
        self.assertEqual([i["itemId"] for i in line["TaxData"]["taxItems"]], [110, 130])

    def test_labour_goes_to_professional_fees_gst_only(self):
        [line] = build_bill(parse_invoice(INV_81031))["billExpenseLineArr"]
        self.assertEqual(line["AccountName"], ACCOUNTS["7660"]["Name"])
        self.assertEqual((line["Amount"], line["TaxCodeId"]), ("55.00", "2"))
        self.assertEqual(line["TaxAmtNonCl"], 0)
        self.assertEqual([i["itemId"] for i in line["TaxData"]["taxItems"]], [110])

    def test_mixed_invoice_gets_one_line_per_tax_letter(self):
        inv = parse_invoice(INV_81384)
        inv["lines"].append({"description": "labour", "tax": "G", "amount": Decimal("55.00")})
        inv["subtotal"] += Decimal("55.00")
        inv["gst"] += Decimal("2.75")
        inv["total"] += Decimal("57.75")
        lines = build_bill(inv)["billExpenseLineArr"]
        self.assertEqual([(l["AccountName"][:4], l["Amount"], l["LineSeq"]) for l in lines],
                         [("7520", "262.20", 1), ("7660", "55.00", 2)])

    def test_account_override_applies_to_every_line(self):
        [line] = build_bill(parse_invoice(INV_81384), account="7620")["billExpenseLineArr"]
        self.assertEqual(line["AccountName"], "7620 - Office Supplies")

    def test_refuses_when_the_total_does_not_match(self):
        inv = parse_invoice(INV_81384)
        inv["total"] = Decimal("300.00")
        with self.assertRaises(BalanceError):
            build_bill(inv)

    def test_refuses_when_the_pdf_pst_disagrees_with_seven_percent(self):
        inv = parse_invoice(INV_81384)
        inv["pst"], inv["total"] = Decimal("10.00"), Decimal("285.31")
        with self.assertRaises(BalanceError):
            build_bill(inv)

    def test_refuses_an_unknown_tax_letter(self):
        inv = parse_invoice(INV_81384)
        inv["lines"][0]["tax"] = "E"
        with self.assertRaises(BalanceError):
            build_bill(inv)


class FiledPath(unittest.TestCase):
    def test_fiscal_year_and_name(self):
        self.assertTrue(filed_path(parse_invoice(INV_81384)).endswith(
            "Formula Resources Group/YE 2027/26 10 INV#81384 293.66.pdf"))

    def test_before_august_stays_in_the_calendar_year(self):
        self.assertTrue(filed_path(parse_invoice(INV_81031)).endswith(
            "Formula Resources Group/YE 2026/26 03 INV#81031 57.75.pdf"))


if __name__ == "__main__":
    unittest.main()
