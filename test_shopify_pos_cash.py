"""Tests for shopify_pos_cash.py — id parsing, month keying and the balance
check. No Shopify calls: the session dicts are real API responses, trimmed."""

import unittest
from decimal import Decimal

from shopify_pos_cash import balances, session_id, _local

TZ = "America/Los_Angeles"


def session(**kw):
    s = {
        "closingTime": "2026-07-31T23:21:55Z",
        "openingBalance": {"amount": "471.55"},
        "expectedOpeningBalance": {"amount": "471.55"},
        "netCashSales": {"amount": "424.00"},
        "totalAdjustments": {"amount": "-17.00"},
        "expectedClosingBalance": {"amount": "878.55"},
        "closingBalance": {"amount": "881.15"},
        "totalDiscrepancy": {"amount": "2.60"},
    }
    s.update(kw)
    return s


class SessionId(unittest.TestCase):
    def test_bare_id(self):
        self.assertEqual(session_id("5120426026"),
                         "gid://shopify/CashTrackingSession/5120426026")

    def test_admin_url(self):
        url = ("https://admin.shopify.com/store/ca-momentumwatch/apps/"
               "point-of-sale-channel/register-sessions/7942963242/activity")
        self.assertEqual(session_id(url),
                         "gid://shopify/CashTrackingSession/7942963242")

    def test_url_segment_beats_a_store_handle_containing_digits(self):
        url = ("https://admin.shopify.com/store/shop-12345678/apps/"
               "point-of-sale-channel/register-sessions/7942963242/activity")
        self.assertEqual(session_id(url),
                         "gid://shopify/CashTrackingSession/7942963242")

    def test_gid_passes_through(self):
        self.assertEqual(session_id("gid://shopify/CashTrackingSession/42424242"),
                         "gid://shopify/CashTrackingSession/42424242")

    def test_rejects_non_ids(self):
        for bad in ("nope", "", "123"):
            with self.assertRaises(ValueError):
                session_id(bad)


class MonthKeying(unittest.TestCase):
    """The API answers in UTC; the month a session belongs to is a local-time
    question. August's session opens just after midnight UTC on the 1st, which is
    still July 31 in the shop's timezone."""

    def test_utc_midnight_is_the_previous_local_day(self):
        self.assertEqual(str(_local("2026-08-01T00:05:04Z", TZ)), "2026-07-31")

    def test_a_close_just_past_month_end_utc_is_still_that_month_locally(self):
        self.assertEqual(str(_local("2026-09-01T00:22:36Z", TZ)), "2026-08-31")

    def test_none_for_an_open_session(self):
        self.assertIsNone(_local(None, TZ))


class Balances(unittest.TestCase):
    def test_a_clean_session_balances(self):
        ok, variance, carried = balances(session())
        self.assertTrue(ok)
        self.assertEqual(variance, Decimal("2.60"))
        self.assertEqual(carried, Decimal("0.00"))

    def test_variance_is_counted_less_expected_not_the_shopify_field(self):
        """Dec 2025: opened at 2514.50 against an expected 0.00. Shopify reports a
        2513.30 discrepancy, which buries the prior-period surprise in this
        month's figure; the month's own variance is -1.20."""
        ok, variance, carried = balances(session(
            openingBalance={"amount": "2514.50"},
            expectedOpeningBalance={"amount": "0.00"},
            netCashSales={"amount": "1252.10"},
            totalAdjustments={"amount": "-2330.00"},
            expectedClosingBalance={"amount": "1436.60"},
            closingBalance={"amount": "1435.40"},
            totalDiscrepancy={"amount": "2513.30"},
        ))
        self.assertTrue(ok)
        self.assertEqual(variance, Decimal("-1.20"))
        self.assertEqual(carried, Decimal("2514.50"))
        self.assertEqual(carried + variance, Decimal("2513.30"))   # = Shopify's field

    def test_identity_failure_is_reported(self):
        ok, _, _ = balances(session(expectedClosingBalance={"amount": "999.99"}))
        self.assertFalse(ok)

    def test_open_session_has_nothing_to_check(self):
        self.assertEqual(balances(session(closingTime=None)), (None, None, None))

    def test_a_missing_closing_count_is_not_treated_as_zero(self):
        self.assertEqual(balances(session(closingBalance=None)), (None, None, None))


if __name__ == "__main__":
    unittest.main()
