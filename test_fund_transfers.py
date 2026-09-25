"""Tests for the PayPal withdrawal -> Fund Transfer payload."""

import datetime
import unittest
from decimal import Decimal

from paypal_deposits import Txn, build_fund_transfer, withdrawals_for


def wd(day, amount, currency="USD"):
    return Txn(transaction_id="T%s" % day, date=datetime.date(2026, 8, day), currency=currency,
               description="User Initiated Withdrawal", gross=Decimal(amount), fee=Decimal(0),
               invoice_id="")


class Withdrawals(unittest.TestCase):
    def test_picks_only_withdrawal_rows(self):
        rows = [wd(4, "-4500.00"),
                Txn("x", datetime.date(2026, 8, 5), "USD", "Express Checkout Payment",
                    Decimal("10"), Decimal("0"), "tok")]
        self.assertEqual([t.transaction_id for t in withdrawals_for(rows)], ["T4"])


class Payload(unittest.TestCase):
    def test_matches_the_captured_shape(self):
        o = build_fund_transfer(wd(4, "-4500.00"), rate="1.39105")
        self.assertEqual(o["Id"], -1)
        self.assertEqual(o["TxnNumber"], -1)
        self.assertEqual(o["TxnDate"], "8/4/2026")
        self.assertEqual(o["TransferFromAccntName"], "1143 - Paypal USD")
        self.assertEqual(o["TransferFromAccntId"], "B7D04105A81AC13AE701924645D2")
        self.assertEqual(o["TransferToAccntName"], "1140 - Umpqua Bank 1729 (USD)")
        self.assertEqual(o["TransferToAccntId"], "B7D04105A81AED1CB3EA3AB9426A")
        self.assertEqual(o["TransferFromAccntCurrencyId"], 1001)
        self.assertEqual(o["TransferToAccntCurrencyId"], 1001)
        self.assertEqual(o["CurrencyId"], "1001")
        self.assertEqual(o["CurrencyCode"], "USD")
        self.assertEqual(o["HomeCurrencyId"], 1)
        self.assertEqual(o["ExchangeRate"], "1.39105")
        self.assertEqual(o["Memo"], "PayPal -> Umpqua")

    def test_amount_is_positive_despite_the_negative_paypal_row(self):
        o = build_fund_transfer(wd(4, "-4500.00"), rate="1.39105")
        self.assertEqual(o["TransferAmount"], "4500.00")
        self.assertEqual(o["FinalTransferAmount"], 4500.0)

    def test_each_transfer_keeps_its_own_date(self):
        self.assertEqual(build_fund_transfer(wd(24, "-3000.00"), rate="1")["TxnDate"], "8/24/2026")
