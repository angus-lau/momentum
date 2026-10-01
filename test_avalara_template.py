"""Tests for avalara_template.py — row selection and VAT arithmetic. No API calls.

The authoritative check is `python3 avalara_template.py --validate`, which rebuilds
the February 2026 filing from the Zonos API and diffs it against the hand-built
`26 02  AvaTemplate.xlsx` (exact match on all 18 shared rows)."""

import unittest
from datetime import date
from decimal import Decimal

from avalara_template import HEADERS, ScopeError, template_rows

REGS = {"IOSS": "IM2500014008", "HMRC": "GB271143234"}


def csv_row(**over):
    r = {"orderReference": "64469", "orderDate": "2026-02-04", "countryCode": "HU",
         "customerName": "Katalin Richter", "orderTotal": "341.82", "currency": "EUR",
         "processing": "CUSTOMS BILL"}
    r.update(over)
    return r


class Selection(unittest.TestCase):
    def test_eu_row_is_filed_under_ioss(self):
        rows, _ = template_rows([csv_row()], REGS)
        self.assertEqual(rows[0]["Transaction type"], "IOSS")
        self.assertEqual(rows[0]["Seller VAT Number"], "IM2500014008")

    def test_gb_row_is_filed_under_hmrc(self):
        rows, _ = template_rows([csv_row(countryCode="GB")], REGS)
        self.assertEqual(rows[0]["Transaction type"], "UK VAT")
        self.assertEqual(rows[0]["Seller VAT Number"], "GB271143234")

    def test_rest_of_world_is_dropped_and_counted(self):
        rows, skipped = template_rows(
            [csv_row(countryCode="AU"), csv_row(countryCode="JP"), csv_row(countryCode="AU")],
            REGS)
        self.assertEqual(rows, [])
        self.assertEqual(skipped, {"AU": 2, "JP": 1})

    def test_both_processing_kinds_are_included(self):
        """February carried 16 CUSTOMS BILL and 2 TAX REMITTANCE rows, so the
        processing column must not filter."""
        rows, _ = template_rows(
            [csv_row(), csv_row(orderReference="64401", processing="TAX REMITTANCE")], REGS)
        self.assertEqual(len(rows), 2)

    def test_missing_registration_is_refused_not_left_blank(self):
        with self.assertRaises(ScopeError):
            template_rows([csv_row()], {"HMRC": "GB271143234"})

    def test_rows_are_ordered_by_date_then_invoice(self):
        rows, _ = template_rows([csv_row(orderReference="2", orderDate="2026-02-10"),
                                 csv_row(orderReference="1", orderDate="2026-02-04")], REGS)
        self.assertEqual([r["Invoice Number"] for r in rows], ["1", "2"])


class Arithmetic(unittest.TestCase):
    def test_hungary_at_27_percent(self):
        r = template_rows([csv_row()], REGS)[0][0]
        self.assertEqual(r["Taxable Basis"], Decimal("341.82"))
        self.assertEqual(r["Value VAT"], Decimal("92.29"))      # 341.82 * 0.27
        self.assertEqual(r["Total"], Decimal("434.11"))

    def test_vat_rate_column_is_the_quotient_not_the_statutory_rate(self):
        """The hand-built file shows 0.26999590... for Hungary, because the column
        divides the rounded VAT by the basis."""
        r = template_rows([csv_row()], REGS)[0][0]
        self.assertEqual(r["VAT rate"], Decimal("92.29") / Decimal("341.82"))
        self.assertNotEqual(r["VAT rate"], Decimal("0.27"))

    def test_rounds_half_up_to_two_places(self):
        # Spain 21% on 289.41 = 60.7761
        r = template_rows([csv_row(countryCode="ES", orderTotal="289.41")], REGS)[0][0]
        self.assertEqual(r["Value VAT"], Decimal("60.78"))

    def test_finland_takes_a_fractional_rate(self):
        r = template_rows([csv_row(countryCode="FI", orderTotal="100.00")], REGS)[0][0]
        self.assertEqual(r["Value VAT"], Decimal("25.50"))

    def test_zero_basis_does_not_divide_by_zero(self):
        r = template_rows([csv_row(orderTotal="0")], REGS)[0][0]
        self.assertEqual((r["Value VAT"], r["VAT rate"]), (Decimal("0.00"), Decimal(0)))

    def test_dates_are_real_dates_in_both_columns(self):
        r = template_rows([csv_row()], REGS)[0][0]
        self.assertEqual(r["Transaction Date"], date(2026, 2, 4))
        self.assertEqual(r["Invoice Date"], date(2026, 2, 4))

    def test_every_header_is_populated(self):
        self.assertEqual(sorted(template_rows([csv_row()], REGS)[0][0]), sorted(HEADERS))


if __name__ == "__main__":
    unittest.main()
