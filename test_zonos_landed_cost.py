"""Tests for zonos_landed_cost.py — row construction. No API calls.

The authoritative check is `python3 zonos_landed_cost.py --validate`, which
regenerates the real 2026-01-01..2026-02-28 dashboard export and diffs every cell
(exact match). These cover the three mappings that were wrong on the first pass,
so a regression is caught without network access."""

import unittest
from decimal import Decimal

from zonos_landed_cost import COLUMNS, build_row, _money


def order(**over):
    o = {
        "accountOrderNumber": "64469",
        "zonosOrderId": "0pcm27kef6vqx",
        "createdAt": "2026-02-04T11:35:45.269Z",
        "currencyCode": "EUR",
        "destinationCountryCode": "HU",
        "trackingNumbers": ["2369071014"],
        "remittance": [],
        "parties": [{"type": "DESTINATION",
                     "person": {"firstName": "Katalin", "lastName": "Richter",
                                "companyName": "Richter Katalin"},
                     "location": {"line1": "Bajtars u.", "line2": "18",
                                  "locality": "Budapest III. kerulet", "administrativeArea": None,
                                  "postalCode": "1039", "countryCode": "HU"}}],
        "amountSubtotals": {"items": 295.0, "shipping": 20.16, "duties": 0.8,
                            "taxes": 15.97, "fees": 9.89, "discounts": 0.0},
        "landedCosts": [{"fees": [{"amount": 9.89, "type": "ADVANCEMENT"}]}],
    }
    o.update(over)
    return o


class Row(unittest.TestCase):
    def test_matches_the_dashboard_export_row(self):
        r = build_row(order())
        self.assertEqual(r["orderTotal"], "341.82")      # 295 + 20.16 + 0.8 + 15.97 + 9.89
        self.assertEqual(r["orderTax"], "15.97")
        self.assertEqual(r["orderDuty"], "0.8")
        self.assertEqual(r["orderFeeAdvancement"], "9.89")
        self.assertEqual(r["customerName"], "Katalin Richter")
        self.assertEqual(r["processing"], "CUSTOMS BILL")

    def test_every_column_is_present(self):
        self.assertEqual(sorted(build_row(order())), sorted(COLUMNS))

    def test_person_name_wins_over_companyName(self):
        """Several records carry a companyName that is the name re-ordered, so
        preferring companyName produced 'Richter Katalin' instead of the export's
        'Katalin Richter'."""
        self.assertEqual(build_row(order())["customerName"], "Katalin Richter")

    def test_companyName_is_the_fallback_when_there_is_no_person_name(self):
        o = order(parties=[{"type": "DESTINATION",
                            "person": {"firstName": None, "lastName": None,
                                       "companyName": "Societe SIE"},
                            "location": {"countryCode": "FR"}}])
        self.assertEqual(build_row(o)["customerName"], "Societe SIE")

    def test_discounts_are_added_not_subtracted(self):
        """`discounts` arrives already negative; subtracting it overstated the
        total (order 64768: the export says 652.86, not 668.46)."""
        o = order(amountSubtotals={"items": 534.0, "shipping": 15.0, "duties": 0.0,
                                   "taxes": 99.98, "fees": 11.68, "discounts": -7.8})
        self.assertEqual(build_row(o)["orderTotal"], "652.86")

    def test_ddp_service_fee_is_the_carrier_fee_column(self):
        o = order(landedCosts=[{"fees": [
            {"amount": 14.0, "type": "ADVANCEMENT"},
            {"amount": 0.79, "type": "CURRENCY_CONVERSION_FEE"},
            {"amount": 11.97, "type": "DDP_SERVICE_FEE"}]}])
        r = build_row(o)
        self.assertEqual((r["orderFeeAdvancement"], r["orderFx"], r["orderFeeCarrier"]),
                         ("14", "0.79", "11.97"))

    def test_remittance_makes_the_row_reportable(self):
        o = order(remittance=[{"amount": 19.95, "description": "IOSS", "taxId": "13170"}])
        self.assertEqual(build_row(o)["processing"], "TAX REMITTANCE")

    def test_an_unmapped_fee_type_warns_instead_of_vanishing(self):
        warn = []
        o = order(landedCosts=[{"fees": [{"amount": 20.0, "type": "COUNTRY"}]}])
        build_row(o, warn)
        self.assertTrue(any("COUNTRY" in w for w in warn))

    def test_fees_of_the_same_column_are_summed(self):
        o = order(landedCosts=[{"fees": [{"amount": 1.5, "type": "GUARANTEE_ORDER"},
                                         {"amount": 2.25, "type": "GUARANTEE_PERCENT"}]}])
        self.assertEqual(build_row(o)["landedCostGuarantee"], "3.75")


class MoneyFormat(unittest.TestCase):
    def test_blank_for_nothing_or_zero(self):
        for v in (None, "", 0, 0.0):
            self.assertEqual(_money(v), "")

    def test_trailing_zeros_trimmed_like_the_export(self):
        self.assertEqual(_money(0.80), "0.8")
        self.assertEqual(_money(2932.0), "2932")
        self.assertEqual(_money(Decimal("341.82")), "341.82")


if __name__ == "__main__":
    unittest.main()
