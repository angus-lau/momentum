"""Tests for dhl_bill.py — the pure parts (no Xoro)."""

import datetime
import json
import pathlib
import tempfile
import unittest

from dhl_bill import read_totals, statement_date, build_bill, vendor_bill_number, NothingToBill

LINES_CSV = """Amount,Check,Freight Amounts,Customer Delivery Fees,Duties & Brokerage Drawbacks,Duties & Brokerage,GST,EU VAT,UK VAT,Invoice Number,Origin,Notes
108.76,0,0,0,0,32.75,76.01,0,0,YVRIR02971777,,
1572.24,0,62.92,0,0,22.57,1486.75,0,0,E103229010400,HKG,
10457.37,,983.16,3466.92,0,821.56,2703.41,1324.68,1157.64,TOTAL,,
"""


class ReadTotals(unittest.TestCase):
    def test_reads_total_row(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "dhl_reconcile_lines.csv").write_text(LINES_CSV)
            t = read_totals(pathlib.Path(d))
        self.assertEqual(t, {"GST": 2703.41, "EU VAT": 1324.68, "UK VAT": 1157.64})

    def test_missing_total_row(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "dhl_reconcile_lines.csv").write_text(LINES_CSV.rsplit("\n", 2)[0] + "\n")
            with self.assertRaises(SystemExit):
                read_totals(pathlib.Path(d))


class StatementDate(unittest.TestCase):
    def test_from_statement_pdf_name(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "2026-09-10.pdf").write_bytes(b"%PDF")
            (pathlib.Path(d) / "108.76 YVRIR02971777.pdf").write_bytes(b"%PDF")
            self.assertEqual(statement_date(pathlib.Path(d)), datetime.date(2026, 9, 10))

    def test_none_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(statement_date(pathlib.Path(d)))


class BuildBill(unittest.TestCase):
    def setUp(self):
        self.totals = {"GST": 2703.41, "EU VAT": 1324.68, "UK VAT": 1157.64}
        self.date = datetime.date(2026, 9, 10)

    def test_matches_ca_b002020(self):
        # the bill created live on 2026-09-21 for the Aug 2026 statement
        bill = build_bill(self.totals, self.date)
        h = bill["billHeader"]
        self.assertEqual(h["VendorId"], "387")
        self.assertEqual((h["TxnDate"], h["BillDate"], h["DueDate"]), ("09/10/2026",) * 3)
        self.assertEqual(h["PaymentTermId"], "1318")
        self.assertEqual(h["StoreId"], "10001")
        self.assertEqual(h["TotalTaxAmt"], 2703.41)
        self.assertEqual(h["TotalAmt"], 5185.73)
        self.assertEqual(h["VendorBillNumber"], "DHL GST-VAT 2026-09-10")
        self.assertEqual(h["TaxAdjItemArr"], [{"Id": 110, "Name": "GST Purchase 5%", "Amount": 2703.41, "Memo": ""}])
        self.assertEqual(h["TaxAdjItemArrLastValid"], h["TaxAdjItemArr"])
        lines = bill["billExpenseLineArr"]
        self.assertEqual([(l["AccountName"], l["Amount"]) for l in lines],
                         [("2251 - VAT NL - Paid", "1324.68"), ("2245 - VAT UK - Paid", "1157.64")])
        self.assertTrue(all(l["TaxCodeId"] == "" and l["TaxAmt"] == 0 for l in lines))
        self.assertEqual([l["LineSeq"] for l in lines], [1, 2])
        self.assertEqual(bill["billItemLineArr"], [])
        json.dumps(bill)  # serialisable as-is

    def test_skips_zero_lines(self):
        bill = build_bill({"GST": 10.0, "EU VAT": 0.0, "UK VAT": 5.0}, self.date)
        self.assertEqual([l["AccountName"] for l in bill["billExpenseLineArr"]], ["2245 - VAT UK - Paid"])
        self.assertEqual(bill["billHeader"]["TotalAmt"], 15.0)

    def test_no_gst_means_no_tax_adjustment(self):
        bill = build_bill({"GST": 0.0, "EU VAT": 100.0, "UK VAT": 0.0}, self.date)
        self.assertEqual(bill["billHeader"]["TaxAdjItemArr"], [])
        self.assertEqual(bill["billHeader"]["TotalTaxAmt"], 0)
        self.assertEqual(bill["billHeader"]["TotalAmt"], 100.0)

    def test_all_zero_raises(self):
        with self.assertRaises(NothingToBill):
            build_bill({"GST": 0.0, "EU VAT": 0.0, "UK VAT": 0.0}, self.date)

    def test_vendor_bill_number(self):
        self.assertEqual(vendor_bill_number(datetime.date(2026, 10, 9)), "DHL GST-VAT 2026-10-09")


if __name__ == "__main__":
    unittest.main()
