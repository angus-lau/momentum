"""Tests for mybill_fetch.py — the pure parts (no browser).

MyBill row parsing, its Django-style dates, and the charge ↔ invoice matcher
with both safeguards (amount equal AND charge date near the invoice due date).
"""

import datetime
import unittest

from mybill_fetch import (
    parse_mybill_date,
    parse_amex_date,
    parse_invoice_rows,
    match_charges,
    pdf_name,
    MatchError,
)


class ParseDates(unittest.TestCase):
    def test_django_abbreviations(self):
        # Django's "N j, Y": Jan. Feb. March April May June July Aug. Sept. Oct. Nov. Dec.
        cases = {
            "Sept. 2, 2026": (2026, 9, 2),
            "Aug. 26, 2026": (2026, 8, 26),
            "July 31, 2026": (2026, 7, 31),
            "March 3, 2026": (2026, 3, 3),
            "May 5, 2026": (2026, 5, 5),
            "Dec. 24, 2025": (2025, 12, 24),
        }
        for raw, ymd in cases.items():
            self.assertEqual(parse_mybill_date(raw), datetime.date(*ymd), raw)

    def test_amex_export_date(self):
        self.assertEqual(parse_amex_date("09 Sep 2026"), datetime.date(2026, 9, 9))
        self.assertEqual(parse_amex_date("31 Aug 2026"), datetime.date(2026, 8, 31))


ROW = (
    '<tr class=" hideOnMobile"><td class="hideOnMobile"><input type="checkbox" class="checkbox multiselect" name="{id}" /></td>'
    '<td class="hideOnMobile"></td>'
    '<td data-header="Account No"><a href="/document/{id}/overview/">971709113</a><span>1606422874</span></td>'
    '<td>1606422874</td><td>St. Moritz Watch Corp. B2C</td>'
    '<td data-header="Invoice No."><a href="/document/{id}/overview/">{no}</a></td>'
    '<td>{type}</td>'
    '<td data-header="Invoice Date">{date}</td>'
    '<td data-header="Due Date">{due}</td>'
    '<td data-header="Status">Paid</td>'
    '<td class="numeric"><span class="price">{total}</span></td>'
    '<td class="numeric"><span class="price">0.00</span></td>'
    '<td class="numeric"><span class="price">{total}</span></td>'
    '<td class="numeric columnBalance"><button data-doc-id="{id}">PDF Invoice</button><span class="price total">0.00</span></td>'
    '<td class="numeric"><span class="price">CAD CAD</span></td></tr>'
)


def row(id, no, type, date, due, total):
    return ROW.format(id=id, no=no, type=type, date=date, due=due, total=total)


class ParseRows(unittest.TestCase):
    def test_parses_fields(self):
        html = "<table>" + row("1666887479", "YVRIR02971777", "Duty invoice", "Aug. 3, 2026", "Aug. 10, 2026", "108.76") + "</table>"
        inv = parse_invoice_rows(html)
        self.assertEqual(len(inv), 1)
        self.assertEqual(inv[0]["id"], "1666887479")
        self.assertEqual(inv[0]["no"], "YVRIR02971777")
        self.assertEqual(inv[0]["type"], "Duty invoice")
        self.assertEqual(inv[0]["date"], datetime.date(2026, 8, 3))
        self.assertEqual(inv[0]["due"], datetime.date(2026, 8, 10))
        self.assertEqual(inv[0]["total"], 108.76)

    def test_thousands_and_credits(self):
        html = row("1680000001", "E103229010400", "Customs invoice", "Aug. 31, 2026", "Sept. 7, 2026", "1,572.24") \
             + row("1670888427", "YVRINR0042591", "US Duty Credit", "Aug. 11, 2026", "", "-1,571.22")
        inv = parse_invoice_rows(html)
        self.assertEqual([i["total"] for i in inv], [1572.24, -1571.22])
        self.assertIsNone(inv[1]["due"])

    def test_skips_rows_without_doc_id(self):
        self.assertEqual(parse_invoice_rows("<tr><td>header</td></tr>"), [])


def inv(id, no, total, due, type="Duty invoice"):
    return {"id": id, "no": no, "type": type, "total": total,
            "date": due - datetime.timedelta(days=7), "due": due}


class Match(unittest.TestCase):
    def test_amount_and_date_both_line_up(self):
        charges = [(datetime.date(2026, 8, 10), 108.76)]
        invoices = [inv("1", "YVRIR02971777", 108.76, datetime.date(2026, 8, 10))]
        m = match_charges(charges, invoices)
        self.assertEqual(m[0]["invoice"]["no"], "YVRIR02971777")

    def test_charge_a_day_after_due_is_fine(self):
        # YVRR003282859: invoice Jul 31, due Aug 14, charged Aug 15
        charges = [(datetime.date(2026, 8, 15), 376.94)]
        invoices = [inv("1", "YVRR003282859", 376.94, datetime.date(2026, 8, 14), "Invoice")]
        self.assertEqual(match_charges(charges, invoices)[0]["invoice"]["id"], "1")

    def test_same_amount_wrong_month_is_rejected(self):
        charges = [(datetime.date(2026, 8, 10), 108.76)]
        invoices = [inv("1", "YVRIR00000001", 108.76, datetime.date(2026, 6, 10))]
        with self.assertRaises(MatchError) as cm:
            match_charges(charges, invoices)
        self.assertIn("108.76", str(cm.exception))

    def test_same_amount_disambiguated_by_date(self):
        charges = [(datetime.date(2026, 8, 10), 50.00), (datetime.date(2026, 9, 1), 50.00)]
        invoices = [inv("A", "YVRIR0000000A", 50.00, datetime.date(2026, 8, 10)),
                    inv("B", "YVRIR0000000B", 50.00, datetime.date(2026, 9, 1))]
        m = match_charges(charges, invoices)
        self.assertEqual([x["invoice"]["id"] for x in m], ["A", "B"])

    def test_two_candidates_in_window_is_ambiguous(self):
        charges = [(datetime.date(2026, 8, 10), 50.00)]
        invoices = [inv("A", "YVRIR0000000A", 50.00, datetime.date(2026, 8, 10)),
                    inv("B", "YVRIR0000000B", 50.00, datetime.date(2026, 8, 11))]
        with self.assertRaises(MatchError) as cm:
            match_charges(charges, invoices)
        self.assertIn("ambiguous", str(cm.exception))

    def test_invoice_used_at_most_once(self):
        # two identical charges, only one invoice -> second charge unmatched
        charges = [(datetime.date(2026, 8, 10), 50.00), (datetime.date(2026, 8, 10), 50.00)]
        invoices = [inv("A", "YVRIR0000000A", 50.00, datetime.date(2026, 8, 10))]
        with self.assertRaises(MatchError):
            match_charges(charges, invoices)

    def test_unmatched_amount(self):
        with self.assertRaises(MatchError) as cm:
            match_charges([(datetime.date(2026, 8, 10), 99.99)], [])
        self.assertIn("99.99", str(cm.exception))

    def test_invoice_without_due_date_falls_back_to_invoice_date(self):
        # no due date shown -> anchor on the invoice date itself
        charges = [(datetime.date(2026, 8, 13), 20.00)]
        i = {"id": "A", "no": "YVRINR0000001", "type": "US Duty Credit", "total": 20.00,
             "date": datetime.date(2026, 8, 11), "due": None}
        self.assertEqual(match_charges(charges, [i])[0]["invoice"]["id"], "A")


class PdfName(unittest.TestCase):
    def test_matches_manual_convention(self):
        self.assertEqual(pdf_name(108.76, "YVRIR02971777"), "108.76 YVRIR02971777.pdf")
        self.assertEqual(pdf_name(1572.24, "E103229010400"), "1572.24 E103229010400.pdf")
        self.assertEqual(pdf_name(16.0, "E103228976100"), "16.00 E103228976100.pdf")


if __name__ == "__main__":
    unittest.main()
