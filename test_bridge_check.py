"""Tests for the 1145 bridging-account check."""

import unittest
from decimal import Decimal

from paypal_deposits import BRIDGE_TOLERANCE, bridge_residual, bridge_rows


def row(name, amount, home, txn="1", date="08/31/2026"):
    return {"F_AccountingName": name, "Amount": amount, "AmountHomeCurrency": home,
            "TxnNumber": txn, "TxnDate": date}


BRIDGE = "1145 - Temporary Bank Account (CAD)"
BRIDGE_ALT = "Temporary Bank Account (CAD)"     # how a fund transfer names it


class Rows(unittest.TestCase):
    def test_matches_both_namings(self):
        rows = bridge_rows([row(BRIDGE, 1, 1, "a"), row(BRIDGE_ALT, 2, 2, "b")])
        self.assertEqual(len(rows), 2)

    def test_ignores_other_accounts(self):
        self.assertEqual(bridge_rows([row("1143 - Paypal USD", 1, 1)]), [])

    def test_deduplicates_repeated_legs(self):
        r = row(BRIDGE, 5, 5, "x")
        self.assertEqual(len(bridge_rows([r, dict(r)])), 1)


class Residual(unittest.TestCase):
    def test_august_clears(self):
        # BD057312 + BD057313 - FT001511
        rows = [row(BRIDGE, 228.56, 228.56, "a"),
                row(BRIDGE, 6419.5148, 8929.8658, "b"),
                row(BRIDGE_ALT, -6583.82, -9158.4228, "c")]
        residual = bridge_residual(rows)
        self.assertEqual(residual, Decimal("0.0030"))
        self.assertLessEqual(abs(residual), BRIDGE_TOLERANCE)

    def test_a_short_transfer_is_caught(self):
        # March 2026: the transfer didn't cover the Service Centre deposit
        rows = [row(BRIDGE, 843.03, 843.03, "a"),
                row(BRIDGE, 4182.94, 5763.464, "b"),
                row(BRIDGE_ALT, -4182.94, -5748.7817, "c")]
        residual = bridge_residual(rows)
        self.assertEqual(residual, Decimal("857.7123"))
        self.assertGreater(abs(residual), BRIDGE_TOLERANCE)

    def test_home_currency_is_used_not_transaction_amount(self):
        rows = [row(BRIDGE, 100, 139.105, "a"), row(BRIDGE_ALT, -100, -139.105, "b")]
        self.assertEqual(bridge_residual(rows), Decimal(0))
