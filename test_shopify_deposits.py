import unittest
from unittest import mock

import shopify_deposits as sd


class FakeClient:
    def get_undeposited_transactions(self, currency_id):
        return []

    def get_data_for_bank_deposit(self):
        return {"HomeCurrencyObj": {"ExchangeRate": 1.41}}


def payout(currency):
    return {"id": 1, "date": "2026-09-28", "amount": 0.0, "currency": currency, "fees": 0.0, "orders": []}


class ExchangeRateTest(unittest.TestCase):
    def rate_for(self, store, currency):
        with mock.patch.object(sd, "get_payout", return_value=payout(currency)):
            res = sd.create_shopify_deposit(amount=0, client=FakeClient(), store=store)
        return res["summary"]["exchange_rate"], res["payload"]["BankDepositHeaderObj"]["ExchangeRate"]

    def test_cad_payout_uses_rate_one(self):
        self.assertEqual(self.rate_for("service_center", "CAD"), ("1", "1"))

    def test_usd_payout_keeps_fetched_rate(self):
        self.assertEqual(self.rate_for("momentum", "USD"), ("1.41", "1.41"))


if __name__ == "__main__":
    unittest.main()
