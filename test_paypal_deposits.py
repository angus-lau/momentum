"""Tests for paypal_deposits.py — classification, grouping and fee maths.

No PayPal/Shopify/Xoro calls; the live path is exercised by a --dry-run.
"""

import datetime
import unittest
from decimal import Decimal

from paypal_deposits import (
    SERVICE_CENTRE,
    in_month,
    cheque_candidates,
    US_STORE,
    Txn,
    classify,
    deposit_memo,
    fee_total,
    group_by_order,
    month_end,
)


def txn(currency="USD", desc="Express Checkout Payment", shop=US_STORE, gross="100.00",
        fee="-3.00", order="68000", tid="T1", invoice="tok"):
    return Txn(transaction_id=tid, date=datetime.date(2026, 8, 15), currency=currency,
               description=desc, gross=Decimal(gross), fee=Decimal(fee),
               invoice_id=invoice, shop_id=shop, order_number=order)


class Classify(unittest.TestCase):
    def test_cad_service_centre_goes_to_its_own_deposit(self):
        self.assertEqual(classify(txn(currency="CAD", shop=SERVICE_CENTRE)), "service_centre")

    def test_usd_rows_go_to_the_usd_native_deposit(self):
        self.assertEqual(classify(txn(currency="USD", shop=US_STORE)), "usd_native")

    def test_non_usd_us_store_rows_go_to_the_combined_deposit(self):
        for cur in ("CAD", "GBP", "EUR", "AUD"):
            self.assertEqual(classify(txn(currency=cur, shop=US_STORE)), "combined", cur)

    def test_usd_service_centre_row_still_usd_native(self):
        # currency decides bridging; a USD row never needs the 1145 bridge
        self.assertEqual(classify(txn(currency="USD", shop=SERVICE_CENTRE)), "usd_native")

    def test_currency_conversion_rows_are_excluded(self):
        self.assertIsNone(classify(txn(desc="General Currency Conversion")))

    def test_withdrawals_are_not_deposits(self):
        self.assertIsNone(classify(txn(desc="User Initiated Withdrawal", order=None, invoice="")))

    def test_refunds_follow_their_currency_like_charges(self):
        self.assertEqual(classify(txn(desc="Payment Refund", currency="EUR")), "combined")


class Grouping(unittest.TestCase):
    def test_several_transactions_on_one_order_group_together(self):
        rows = [txn(tid="a", order="68001", gross="50.00"),
                txn(tid="b", order="68001", gross="-20.00", desc="Payment Refund"),
                txn(tid="c", order="68002")]
        g = group_by_order(rows)
        self.assertEqual(sorted(g), ["68001", "68002"])
        self.assertEqual(len(g["68001"]), 2)

    def test_unmatched_rows_are_kept_apart(self):
        rows = [txn(tid="a", order=None), txn(tid="b", order="68001")]
        g = group_by_order(rows)
        self.assertNotIn(None, g)
        self.assertEqual(list(g), ["68001"])


class ChequeNumbers(unittest.TestCase):
    def test_service_centre_prefix_is_stripped(self):
        # Shopify names the order C36337; Xoro holds it as 36337 (ref SC-CD…)
        self.assertEqual(cheque_candidates("C36337"), ["C36337", "36337"])

    def test_plain_order_number_unchanged(self):
        self.assertEqual(cheque_candidates("68062"), ["68062"])

    def test_blank(self):
        self.assertEqual(cheque_candidates(None), [""])


class InMonth(unittest.TestCase):
    LAST = datetime.date(2026, 8, 31)

    def test_row_inside_the_month(self):
        self.assertTrue(in_month({"TxnDate": "08/20/2026"}, self.LAST))

    def test_later_refund_belongs_to_its_own_month(self):
        # order 68503: sold in August, refunded 09/10 -> the refund is September's
        self.assertFalse(in_month({"TxnDate": "09/10/2026"}, self.LAST))

    def test_earlier_row_is_kept(self):
        self.assertTrue(in_month({"TxnDate": "07/31/2026"}, self.LAST))

    def test_unparseable_date_is_kept_for_review(self):
        self.assertTrue(in_month({"TxnDate": ""}, self.LAST))


class Fees(unittest.TestCase):
    def test_sums_fees_at_face_value(self):
        self.assertEqual(fee_total([txn(fee="-3.00"), txn(fee="-1.50")]), Decimal("-4.50"))

    def test_excludes_unmatched_transactions(self):
        # a transaction with no order has nothing to offset its fee against
        rows = [txn(fee="-3.00"), txn(fee="-9.99", order=None)]
        self.assertEqual(fee_total([r for r in rows if r.order_number]), Decimal("-3.00"))

    def test_refund_legs_keep_their_own_fee(self):
        self.assertEqual(fee_total([txn(fee="-3.00"), txn(desc="Payment Refund", fee="0.00")]),
                         Decimal("-3.00"))


class Memos(unittest.TestCase):
    def test_service_centre_memo(self):
        self.assertEqual(deposit_memo("service_centre", datetime.date(2026, 8, 31), ["CAD"]),
                         "PayPal Service Centre Payout - August 2026")

    def test_combined_memo_lists_contributing_currencies(self):
        self.assertEqual(deposit_memo("combined", datetime.date(2026, 8, 31), ["CAD", "GBP", "EUR"]),
                         "PayPal Payout CAD, GBP, EUR - August 2026")

    def test_month_end(self):
        self.assertEqual(month_end("2026-08"), datetime.date(2026, 8, 31))
        self.assertEqual(month_end("2027-02"), datetime.date(2027, 2, 28))


if __name__ == "__main__":
    unittest.main()
