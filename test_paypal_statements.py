"""Tests for paypal_statements.py — the pure parts (no PayPal calls).

Field mapping from the Transaction Search API onto PayPal's own CSV layout,
month bounds, fiscal-year folder and the report filename.
"""

import datetime
import unittest

from paypal_statements import (
    CSV_COLUMNS,
    rows_for,
    amount,
    description_for,
    fiscal_year_folder,
    local_date_time,
    month_bounds,
    report_filename,
    row_for,
)


class Description(unittest.TestCase):
    def test_known_event_codes(self):
        self.assertEqual(description_for("T0006"), "Express Checkout Payment")
        self.assertEqual(description_for("T0200"), "General Currency Conversion")
        self.assertEqual(description_for("T0403"), "User Initiated Withdrawal")
        self.assertEqual(description_for("T1107"), "Payment Refund")

    def test_unknown_code_is_surfaced_not_guessed(self):
        self.assertEqual(description_for("T9999"), "T9999")


class Amounts(unittest.TestCase):
    def test_thousands_separator_like_paypals_csv(self):
        self.assertEqual(amount("-4500.00"), "-4,500.00")
        self.assertEqual(amount("2869.26"), "2,869.26")
        self.assertEqual(amount("352.63"), "352.63")

    def test_missing_is_zero(self):
        self.assertEqual(amount(None), "0.00")
        self.assertEqual(amount(""), "0.00")


class LocalTime(unittest.TestCase):
    def test_utc_converted_to_vancouver(self):
        # 2026-08-01T09:52:25Z is 02:52:25 in America/Vancouver (PDT)
        d, t = local_date_time("2026-08-01T09:52:25Z")
        self.assertEqual((d, t), ("01/08/2026", "02:52:25"))

    def test_day_rolls_back_across_midnight(self):
        # 2026-08-01T02:00:00Z is still 31 July locally
        d, t = local_date_time("2026-08-01T02:00:00Z")
        self.assertEqual(d, "31/07/2026")


class Bounds(unittest.TestCase):
    def test_month_bounds(self):
        s, e = month_bounds("2026-08")
        self.assertEqual(s, "2026-08-01T00:00:00-0000")
        self.assertEqual(e, "2026-08-31T23:59:59-0000")

    def test_february(self):
        s, e = month_bounds("2027-02")
        self.assertEqual(e, "2027-02-28T23:59:59-0000")


class Folder(unittest.TestCase):
    def test_fiscal_year_ends_july(self):
        self.assertEqual(fiscal_year_folder(2026, 7), "FY2026")
        self.assertEqual(fiscal_year_folder(2026, 8), "FY2027")
        self.assertEqual(fiscal_year_folder(2027, 7), "FY2027")

    def test_report_filename_matches_paypals_convention(self):
        name = report_filename("GH4H22C7H8HBL", "2026-08", datetime.datetime(2026, 9, 22, 9, 36, 58))
        self.assertEqual(name, "GH4H22C7H8HBL-CSR-20260801000000-20260831235959-20260922093658.CSV")


class Ordering(unittest.TestCase):
    def _txn(self, tid, cur, iso, code="T0006"):
        return {"transaction_info": {"transaction_id": tid, "transaction_event_code": code,
                                     "transaction_initiation_date": iso,
                                     "transaction_amount": {"currency_code": cur, "value": "1.00"}}}

    def test_primary_currency_block_first_then_alphabetical(self):
        rows = rows_for([self._txn("g", "GBP", "2026-08-01T12:00:00Z"),
                         self._txn("c", "CAD", "2026-08-01T12:00:00Z"),
                         self._txn("u", "USD", "2026-08-02T12:00:00Z"),
                         self._txn("e", "EUR", "2026-08-01T12:00:00Z")])
        self.assertEqual([r["Currency"] for r in rows], ["USD", "CAD", "EUR", "GBP"])

    def test_each_block_is_oldest_first(self):
        rows = rows_for([self._txn("b", "USD", "2026-08-09T12:00:00Z"),
                         self._txn("a", "USD", "2026-08-02T12:00:00Z")])
        self.assertEqual([r["Transaction ID"] for r in rows], ["a", "b"])

    def test_derived_row_inherits_parent_shipping(self):
        parent = self._txn("p", "USD", "2026-08-01T12:00:00Z")
        parent["transaction_info"]["shipping_amount"] = {"currency_code": "USD", "value": "6.00"}
        child = self._txn("c", "USD", "2026-08-01T12:00:01Z", code="T0200")
        child["transaction_info"]["paypal_reference_id"] = "p"
        rows = {r["Transaction ID"]: r for r in rows_for([parent, child])}
        self.assertEqual(rows["c"]["Shipping and Handling Amount"], "6.00")


class RowMapping(unittest.TestCase):
    INFO = {
        "transaction_id": "27V8742079121382K",
        "transaction_event_code": "T0006",
        "transaction_initiation_date": "2026-08-01T15:30:18Z",
        "transaction_amount": {"currency_code": "USD", "value": "282.00"},
        "fee_amount": {"currency_code": "USD", "value": "-9.89"},
        "ending_balance": {"currency_code": "USD", "value": "3141.37"},
        "invoice_id": "rflPSnwHV1WaF9kqjdNcWbh23",
        "paypal_reference_id": "",
    }
    PAYER = {"email_address": "goatrows@att.net", "payer_name": {"alternate_full_name": "Brad Colaw"}}

    def test_maps_onto_paypals_columns(self):
        r = row_for({"transaction_info": self.INFO, "payer_info": self.PAYER})
        self.assertEqual(list(r.keys()), CSV_COLUMNS)
        self.assertEqual(r["Date"], "01/08/2026")
        self.assertEqual(r["Time"], "08:30:18")
        self.assertEqual(r["Time Zone"], "America/Vancouver")
        self.assertEqual(r["Description"], "Express Checkout Payment")
        self.assertEqual(r["Currency"], "USD")
        self.assertEqual(r["Gross "], "282.00")
        self.assertEqual(r["Fee "], "-9.89")
        self.assertEqual(r["Net"], "272.11")           # gross + fee, fee already negative
        self.assertEqual(r["Balance"], "3,141.37")
        self.assertEqual(r["From Email Address"], "goatrows@att.net")
        self.assertEqual(r["Name"], "Brad Colaw")
        self.assertEqual(r["Invoice ID"], "rflPSnwHV1WaF9kqjdNcWbh23")
        self.assertEqual(r["Reference Txn ID"], "")   # blank for T0006, per PayPal's export

    def test_reference_txn_id_only_for_derived_transactions(self):
        for code, expected in (("T0006", ""), ("T0403", ""),
                               ("T0200", "9E2399120U6232151"), ("T1107", "9E2399120U6232151")):
            info = dict(self.INFO, transaction_event_code=code, paypal_reference_id="9E2399120U6232151")
            self.assertEqual(row_for({"transaction_info": info})["Reference Txn ID"], expected, code)

    def test_shipping_and_sales_tax(self):
        info = dict(self.INFO, shipping_amount={"currency_code": "USD", "value": "25.00"},
                    sales_tax_amount={"currency_code": "CAD", "value": "17.65"})
        r = row_for({"transaction_info": info})
        self.assertEqual(r["Shipping and Handling Amount"], "25.00")
        self.assertEqual(r["Sales Tax"], "-17.65")   # the export negates the API's value

    def test_blank_payer_and_no_fee(self):
        info = dict(self.INFO, transaction_event_code="T0403", fee_amount=None,
                    transaction_amount={"currency_code": "USD", "value": "-4500.00"},
                    invoice_id=None, ending_balance={"currency_code": "USD", "value": "945.10"})
        r = row_for({"transaction_info": info})
        self.assertEqual(r["Description"], "User Initiated Withdrawal")
        self.assertEqual(r["Gross "], "-4,500.00")
        self.assertEqual(r["Fee "], "0.00")
        self.assertEqual(r["Net"], "-4,500.00")
        self.assertEqual((r["From Email Address"], r["Name"], r["Invoice ID"]), ("", "", ""))

    def test_bank_columns_always_blank(self):
        r = row_for({"transaction_info": self.INFO, "payer_info": self.PAYER})
        self.assertEqual(r["Bank Name"], "")
        self.assertEqual(r["Bank Account"], "")


if __name__ == "__main__":
    unittest.main()
