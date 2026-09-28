"""Tests for ceridian_bills.py — parsing and bill construction (no Xoro calls)."""

import datetime
import unittest
from decimal import Decimal

from ceridian_bills import (
    ACCOUNTS,
    BalanceError,
    build_bill,
    gst_on,
    parse_funds_summary,
    parse_journal_entry,
    period_date,
)

# The Journal Entry page of 20260815.pdf, verbatim.
JE_AUG15 = """Journal Entry
Dept.Account No. Debit Credit Description Accrued
9972 7840 65.22 LTD*
9972 7840 30.86 *LIFE
9972 7840 2.70 *AD&D
9989 1160 2,713.56 FED.TAX
9990 1160 1,706.68 C.P.P.
9993 1160 450.17 E.I.
9994 1160 15,525.02 NET PAY
9996 2270 140.02 ACCRUED VAC
20,634.23 PAYROLL CLEARING ACCOUNT .00
100 7120 7,866.68 SAL. AND WAGES
14.26 EMPLOYEE BENEFITS
7880 442.88 CPP/QPP
7900 179.52 E.I.
200 7380 4,288.16 SAL. AND WAGES
8.40 EMPLOYEE BENEFITS
7880 238.29 CPP/QPP
7900 42.62 E.I.
2270 71.82 ACCRUED VAC
300 7780 7,189.87 SAL. AND WAGES
10.90 EMPLOYEE BENEFITS
7880 172.17 CPP/QPP
7900 40.46 E.I.
2270 68.20 ACCRUED VAC
20,634.23 PAYROLL CLEARING ACCOUNT .00"""

FS_AUG15 = """Funds Summary
DEPOSITS DR $15,525.02
REMITTANCES DR $4,870.41
CHEQUES DR $0.00
SERVICE CHARGES DR $67.89
TOTAL PAYMENT DUE ........................................................... $20,463.32 business day
GST Number 87371 1170 RT0001 Invoice Number 239271-452 Labour Day
G. S. T. 3.23
TOTAL 67.89 PayrollFunding: blah"""


class FundsSummary(unittest.TestCase):
    def test_pulls_invoice_total_and_fee(self):
        fs = parse_funds_summary(FS_AUG15)
        self.assertEqual(fs["invoice_number"], "239271-452")
        self.assertEqual(fs["total_due"], Decimal("20463.32"))
        self.assertEqual(fs["gst"], Decimal("3.23"))
        self.assertEqual(fs["service_fee"], Decimal("64.66"))   # 67.89 total less 3.23 GST

    def test_gst_is_five_percent_rounded(self):
        self.assertEqual(gst_on(Decimal("64.66")), Decimal("3.23"))
        self.assertEqual(gst_on(Decimal("60.46")), Decimal("3.02"))


class JournalEntry(unittest.TestCase):
    def test_departments_and_their_lines(self):
        je = parse_journal_entry(JE_AUG15)
        self.assertEqual(sorted(je["departments"]), ["100", "200", "300"])
        d100 = je["departments"]["100"]
        self.assertEqual(d100["7120"], Decimal("7866.68"))
        self.assertEqual(d100["7880"], Decimal("442.88"))
        self.assertEqual(d100["7900"], Decimal("179.52"))

    def test_each_department_uses_its_own_salary_account(self):
        je = parse_journal_entry(JE_AUG15)
        self.assertIn("7380", je["departments"]["200"])
        self.assertIn("7780", je["departments"]["300"])

    def test_benefits_are_ltd_and_std_only(self):
        # *LIFE and *AD&D sit in the same 7840 block but are not on the bill
        je = parse_journal_entry(JE_AUG15)
        self.assertEqual(je["benefits"], Decimal("65.22"))

    def test_excludes_accrued_vac_and_the_clearing_credits(self):
        je = parse_journal_entry(JE_AUG15)
        for dept in je["departments"].values():
            self.assertNotIn("2270", dept)
            self.assertNotIn("1160", dept)

    def test_excludes_the_unlabelled_employee_benefits_subline(self):
        # 14.26 / 8.40 / 10.90 are part of the JE but not of the bill
        je = parse_journal_entry(JE_AUG15)
        self.assertNotIn(Decimal("14.26"), je["departments"]["100"].values())

    def test_no_shareholder_deduction_when_absent(self):
        self.assertEqual(parse_journal_entry(JE_AUG15)["sp_dedns"], Decimal(0))

    def test_shareholder_deduction_when_present(self):
        je = parse_journal_entry(JE_AUG15 + "\n9972 2900 767.33 SP.DEDNS")
        self.assertEqual(je["sp_dedns"], Decimal("767.33"))

    def test_std_is_added_to_ltd(self):
        je = parse_journal_entry(JE_AUG15 + "\n9972 7840 189.12 STD*")
        self.assertEqual(je["benefits"], Decimal("254.34"))


class BuildBill(unittest.TestCase):
    def setUp(self):
        self.fs = parse_funds_summary(FS_AUG15)
        self.je = parse_journal_entry(JE_AUG15)
        self.date = datetime.date(2026, 8, 15)

    def test_balances_to_the_total_payment_due(self):
        bill = build_bill(self.fs, self.je, self.date)
        self.assertEqual(Decimal(str(bill["billHeader"]["TotalAmt"])), self.fs["total_due"])

    def test_header_fields(self):
        h = build_bill(self.fs, self.je, self.date)["billHeader"]
        self.assertEqual(h["VendorId"], "317")
        self.assertEqual(h["PaymentTermId"], "1285")           # Due on receipt
        self.assertEqual((h["TxnDate"], h["BillDate"], h["DueDate"]), ("08/15/2026",) * 3)
        self.assertEqual(h["VendorBillNumber"], "239271-452")
        self.assertEqual(Decimal(str(h["TotalTaxAmt"])), Decimal("3.23"))

    def test_service_fee_line_carries_the_gst_tax_code(self):
        lines = build_bill(self.fs, self.je, self.date)["billExpenseLineArr"]
        fee = next(l for l in lines if l["AccountId"] == ACCOUNTS["7660"]["Id"])
        self.assertEqual(fee["Amount"], "64.66")
        self.assertEqual(fee["TaxCodeId"], "2")
        self.assertEqual(fee["TaxData"]["taxItems"][0]["itemRatePerc"], 5)

    def test_benefits_line_is_negative(self):
        lines = build_bill(self.fs, self.je, self.date)["billExpenseLineArr"]
        ben = next(l for l in lines if l["AccountId"] == ACCOUNTS["7840"]["Id"])
        self.assertEqual(ben["Amount"], "-65.22")

    def test_one_line_per_department_component(self):
        lines = build_bill(self.fs, self.je, self.date)["billExpenseLineArr"]
        # 3 departments x (salary + CPP + EI) = 9, plus the fee and benefits lines
        self.assertEqual(len(lines), 11)

    def test_line_sequence_is_numbered_from_one(self):
        lines = build_bill(self.fs, self.je, self.date)["billExpenseLineArr"]
        self.assertEqual([l["LineSeq"] for l in lines], list(range(1, len(lines) + 1)))

    def test_refuses_a_bill_that_does_not_balance(self):
        je = dict(self.je)
        je["benefits"] = Decimal("999.99")
        with self.assertRaises(BalanceError):
            build_bill(self.fs, je, self.date)


class PeriodDate(unittest.TestCase):
    def test_from_filename(self):
        self.assertEqual(period_date("20260815.pdf"), datetime.date(2026, 8, 15))
        self.assertEqual(period_date("/x/y/20260915.PDF"), datetime.date(2026, 9, 15))


if __name__ == "__main__":
    unittest.main()
