"""Tests for pos_cash_statement.py — statement line construction from a POS
register session. Pure: no Shopify or Xoro calls. The August session is real."""

import unittest
from datetime import date
from decimal import Decimal

from pos_cash_statement import StatementError, fiscal_year, lines_for_session, month_folder

TZ = "America/Los_Angeles"

# Session 8075542570, trimmed to what the statement needs.
AUG = {
    "id": "gid://shopify/CashTrackingSession/8075542570",
    "openingTime": "2026-08-01T00:05:04Z",
    "closingTime": "2026-09-01T00:22:36Z",
    "openingBalance": {"amount": "881.15"},
    "expectedOpeningBalance": {"amount": "881.15"},
    "closingBalance": {"amount": "1323.25"},
    "expectedClosingBalance": {"amount": "1325.05"},
    "netCashSales": {"amount": "474.90"},
    "totalAdjustments": {"amount": "-31.00"},
    "totalDiscrepancy": {"amount": "-1.80"},
    "adjustments": {"edges": [
        {"node": {"cash": {"amount": "-23.00"}, "note": "Coffee", "time": "2026-08-05T16:26:37Z"}},
        {"node": {"cash": {"amount": "-8.00"}, "note": "Office Coffee Filters ",
                  "time": "2026-08-18T16:27:45Z"}},
    ]},
    "cashTransactions": {"edges": [
        {"node": {"kind": "SALE", "order": {"name": "C36335"},
                  "amountSet": {"presentmentMoney": {"amount": "28.0"}}}},
        {"node": {"kind": "SALE", "order": {"name": "C36365"},
                  "amountSet": {"presentmentMoney": {"amount": "33.6"}}}},
        {"node": {"kind": "SALE", "order": {"name": "C36432"},
                  "amountSet": {"presentmentMoney": {"amount": "11.2"}}}},
        {"node": {"kind": "SALE", "order": {"name": "C36474"},
                  "amountSet": {"presentmentMoney": {"amount": "56.0"}}}},
        {"node": {"kind": "SALE", "order": {"name": "C36522"},
                  "amountSet": {"presentmentMoney": {"amount": "16.8"}}}},
        {"node": {"kind": "SALE", "order": {"name": "C36579"},
                  "amountSet": {"presentmentMoney": {"amount": "28.0"}}}},
        {"node": {"kind": "SALE", "order": {"name": "C36593"},
                  "amountSet": {"presentmentMoney": {"amount": "22.4"}}}},
        {"node": {"kind": "SALE", "order": {"name": "C36608"},
                  "amountSet": {"presentmentMoney": {"amount": "278.88"}}}},
    ]},
}


def build(**over):
    s = dict(AUG)
    s.update(over)
    return lines_for_session(s, "2026-08", TZ)


class AugustStatement(unittest.TestCase):
    def setUp(self):
        self.lines, self.opening, self.closing, _ = build()

    def test_spans_the_counted_balances(self):
        self.assertEqual(self.opening, Decimal("881.15"))
        self.assertEqual(self.closing, Decimal("1323.25"))
        self.assertEqual(self.opening + sum(l["amount"] for l in self.lines), self.closing)

    def test_five_lines_oldest_first(self):
        self.assertEqual([(l["date"].isoformat(), str(l["amount"]), l["description"])
                          for l in self.lines],
                         [("2026-08-05", "-23.00", "Coffee"),
                          ("2026-08-18", "-8.00", "Office Coffee Filters"),
                          ("2026-08-31", "474.88", "POS cash sales"),
                          ("2026-08-31", "0.02", "Penny rounding adjustment"),
                          ("2026-08-31", "-1.80", "Cash count variance")])

    def test_sales_line_is_the_transactions_not_the_reported_total(self):
        """The deposit totals the actual payments (474.88); Shopify's 474.90 includes
        nickel rounding, which gets its own line so the deposit line still matches."""
        sales = next(l for l in self.lines if l["description"] == "POS cash sales")
        self.assertEqual(sales["amount"], Decimal("474.88"))

    def test_adjustment_keeps_its_own_date_in_shop_local_time(self):
        coffee = next(l for l in self.lines if l["description"] == "Coffee")
        self.assertEqual(coffee["date"], date(2026, 8, 5))

    def test_a_refund_reduces_the_sales_line(self):
        txns = {"edges": AUG["cashTransactions"]["edges"] + [
            {"node": {"kind": "REFUND", "order": {"name": "C36600"},
                      "amountSet": {"presentmentMoney": {"amount": "20.0"}}}}]}
        # a refund shifts the sales line down, so the balances must move with it
        with self.assertRaises(StatementError):
            build(cashTransactions=txns)


class Refusals(unittest.TestCase):
    def test_refuses_a_truncated_adjustment_list(self):
        """The span check is what catches this. It cannot catch a wrong closing
        balance — the variance line is computed as counted less expected, so it
        absorbs any closing figure — but if the adjustments come back short of
        ``totalAdjustments`` (paging), the lines no longer reach the closing count."""
        one = {"edges": AUG["adjustments"]["edges"][:1]}
        with self.assertRaises(StatementError) as cm:
            build(adjustments=one)
        self.assertIn("needs", str(cm.exception))

    def test_a_wrong_closing_balance_is_absorbed_by_the_variance_line(self):
        lines, opening, closing, _ = build(closingBalance={"amount": "9999.99"})
        var = next(l for l in lines if l["description"] == "Cash count variance")
        self.assertEqual(var["amount"], Decimal("8674.94"))
        self.assertEqual(opening + sum(l["amount"] for l in lines), closing)

    def test_refuses_a_prior_period_opening_surprise(self):
        """Dec 2025's shape: the 2514.50 is a prior-period correction and must not
        be swept into this month's statement."""
        with self.assertRaises(StatementError) as cm:
            build(expectedOpeningBalance={"amount": "0.00"})
        self.assertIn("PRIOR", str(cm.exception))

    def test_refuses_a_cash_difference_too_large_to_be_rounding(self):
        # keep the session's own arithmetic intact, or that check fires first
        with self.assertRaises(StatementError) as cm:
            build(netCashSales={"amount": "480.00"},
                  expectedClosingBalance={"amount": "1330.15"},
                  closingBalance={"amount": "1328.35"})
        self.assertIn("rounding", str(cm.exception))

    def test_refuses_a_session_whose_own_arithmetic_fails(self):
        with self.assertRaises(StatementError):
            build(expectedClosingBalance={"amount": "1.00"})


class Filing(unittest.TestCase):
    def test_august_files_into_the_next_fiscal_year(self):
        """FY ends 31 July, so 2026-08 is FY2027."""
        self.assertEqual(fiscal_year(date(2026, 8, 1)), 2027)
        self.assertEqual(fiscal_year(date(2026, 7, 31)), 2026)
        self.assertTrue(month_folder("2026-08").endswith("FY2027/Service Centre Cash/26 08"))


if __name__ == "__main__":
    unittest.main()
