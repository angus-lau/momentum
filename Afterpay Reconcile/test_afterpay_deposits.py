"""Tests for afterpay_deposits.py — settlement grouping, bank matching, fee maths.

No Shopify/Xoro calls here; those are exercised by a --dry-run against real data.
"""

import datetime
import unittest
from decimal import Decimal

from afterpay_deposits import (
    cents,
    group_settlements,
    match_bank_rows,
    fee_for,
    MatchError,
)


def row(settle, net, typ="Order", token="tok", gross="100.00", fee="5.00"):
    return {"Settlement Date": settle, "Net Settlement Amount": net, "Type": typ,
            "Merchant Order ID": token, "Order Amount": gross, "Merchant Fee incl Tax": fee}


class Cents(unittest.TestCase):
    def test_half_up_not_bankers(self):
        # Afterpay nets 311.00 - 14.295 = 296.705 and pays 296.71; Python's round() gives 296.70
        self.assertEqual(cents(Decimal("296.705")), Decimal("296.71"))
        self.assertEqual(cents(Decimal("0.125")), Decimal("0.13"))
        self.assertEqual(cents(Decimal("-297.2700")), Decimal("-297.27"))


class Grouping(unittest.TestCase):
    def test_sums_same_settlement_date(self):
        g = group_settlements([row("08/11/2026", "$297.27", token="a"),
                               row("08/11/2026", "$297.23", token="b"),
                               row("08/12/2026", "-$297.27", typ="Refund", token="a")])
        self.assertEqual([d for d, _ in g], [datetime.date(2026, 8, 11), datetime.date(2026, 8, 12)])
        self.assertEqual(g[0][1]["net"], Decimal("594.50"))
        self.assertEqual(len(g[0][1]["rows"]), 2)
        self.assertEqual(g[1][1]["net"], Decimal("-297.27"))

    def test_keeps_raw_precision(self):
        g = group_settlements([row("08/01/2026", "$296.705000")])
        self.assertEqual(g[0][1]["net"], Decimal("296.705000"))


class Matching(unittest.TestCase):
    def setUp(self):
        self.groups = group_settlements([
            row("07/30/2026", "$340.635000", token="jul"),
            row("08/01/2026", "$296.705000", token="a"),
            row("08/10/2026", "$411.543700", token="b"),
            row("08/12/2026", "-$297.270000", typ="Refund", token="c"),
            row("08/17/2026", "$436.255200", token="d"),
        ])

    def test_one_settlement_per_deposit(self):
        m = match_bank_rows([(datetime.date(2026, 8, 4), Decimal("296.71"))], self.groups)
        self.assertEqual(len(m), 1)
        self.assertEqual([d for d, _ in m[0]["groups"]], [datetime.date(2026, 8, 1)])

    def test_skips_settlement_paid_before_the_window(self):
        # the 07/30 settlement was deposited before this bank export starts
        m = match_bank_rows([(datetime.date(2026, 8, 4), Decimal("296.71"))], self.groups)
        self.assertEqual(m[0]["skipped_before"], [datetime.date(2026, 7, 30)])

    def test_refund_carried_into_the_next_deposit(self):
        # 08/18 deposit = -297.27 (08/12 refund) + 436.2552 (08/17) = 138.9852 -> 138.99
        m = match_bank_rows([(datetime.date(2026, 8, 18), Decimal("138.99"))], self.groups)
        self.assertEqual([d for d, _ in m[0]["groups"]],
                         [datetime.date(2026, 8, 12), datetime.date(2026, 8, 17)])

    def test_never_reuses_a_settlement(self):
        m = match_bank_rows([(datetime.date(2026, 8, 4), Decimal("296.71")),
                             (datetime.date(2026, 8, 11), Decimal("411.54"))], self.groups)
        self.assertEqual([d for d, _ in m[0]["groups"]], [datetime.date(2026, 8, 1)])
        self.assertEqual([d for d, _ in m[1]["groups"]], [datetime.date(2026, 8, 10)])

    def test_ignores_settlements_after_the_bank_date(self):
        with self.assertRaises(MatchError):
            match_bank_rows([(datetime.date(2026, 8, 2), Decimal("411.54"))], self.groups)

    def test_unmatched_amount_raises_with_detail(self):
        with self.assertRaises(MatchError) as cm:
            match_bank_rows([(datetime.date(2026, 8, 20), Decimal("999.99"))], self.groups)
        self.assertIn("999.99", str(cm.exception))


class Fee(unittest.TestCase):
    def test_fee_is_gross_minus_bank(self):
        # absorbs Afterpay's own half-up rounding: 311.00 - 296.71 = 14.29 (stated 14.295)
        self.assertEqual(fee_for(Decimal("311.00"), Decimal("296.71")), Decimal("14.29"))

    def test_refund_leg_reduces_gross(self):
        # 08/18: gross 457.44 + (-297.27) = 160.17, bank 138.99 -> fee 21.18 (stated 21.19)
        self.assertEqual(fee_for(Decimal("160.17"), Decimal("138.99")), Decimal("21.18"))

    def test_exact_when_no_rounding(self):
        self.assertEqual(fee_for(Decimal("629.37"), Decimal("594.50")), Decimal("34.87"))


if __name__ == "__main__":
    unittest.main()
