"""Tests for the GL-derived exchange rate helper."""

import unittest

from xoro_api import AmbiguousRate, pick_rate


def row(name, amount, home):
    return {"F_AccountingName": name, "Amount": amount, "AmountHomeCurrency": home}


class PickRate(unittest.TestCase):
    def test_home_currency_is_one(self):
        self.assertEqual(pick_rate([], "CAD"), 1)

    def test_dominant_rate_wins(self):
        rows = [row("Umpqua Bank 1729 (USD)", 100, 139.105) for _ in range(5)]
        rows += [row("1211 - Undeposited Funds (USD)", 50, 69.64)]   # 1.3928, one row
        self.assertEqual(pick_rate(rows, "USD"), 1.39105)

    def test_other_currencies_are_excluded(self):
        # a GBP posting must not pollute the USD rate
        rows = [row("Umpqua Bank 1729 (USD)", 100, 139.105) for _ in range(3)]
        rows += [row("1151 - HSBC (GBP)", 100, 160.779) for _ in range(9)]
        self.assertEqual(pick_rate(rows, "USD"), 1.39105)

    def test_tie_refuses_to_guess(self):
        rows = [row("x (USD)", 100, 139.315) for _ in range(3)]
        rows += [row("y (USD)", 100, 139.545) for _ in range(3)]
        with self.assertRaises(AmbiguousRate) as cm:
            pick_rate(rows, "USD")
        self.assertIn("tied", str(cm.exception))

    def test_tie_can_be_resolved_to_the_lowest_on_request(self):
        rows = [row("x (USD)", 100, 139.315) for _ in range(3)]
        rows += [row("y (USD)", 100, 139.545) for _ in range(3)]
        self.assertEqual(pick_rate(rows, "USD", on_tie="lowest"), 1.39315)

    def test_on_tie_does_not_change_a_clear_winner(self):
        rows = [row("x (USD)", 100, 139.105) for _ in range(5)]
        rows += [row("y (USD)", 100, 139.545)]
        self.assertEqual(pick_rate(rows, "USD", on_tie="lowest"), 1.39105)

    def test_no_postings_raises(self):
        with self.assertRaises(AmbiguousRate):
            pick_rate([row("BMO (CAD)", 100, 100)], "USD")

    def test_tiny_amounts_ignored(self):
        rows = [row("x (USD)", 0.01, 0.0139)]
        rows += [row("x (USD)", 100, 139.105) for _ in range(2)]
        self.assertEqual(pick_rate(rows, "USD"), 1.39105)
