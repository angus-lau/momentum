"""Stripe payout -> Xoro deposit matching (no network)."""
import unittest

from stripe_deposits import build_deposit


def payout(*items):
    return {"id": "po_x", "date": "2026-09-29", "amount": sum(a for _, a in items), "currency": "CAD",
            "fees": 0.0, "items": [{"ref": r, "amount": a, "type": "refund"} for r, a in items], "adjustments": []}


def row(ref, amount, typ="Refund Customer Credit"):
    return {"LineRefNo": ref, "Amount": amount, "TxnTypeName": typ}


class RefundFallback(unittest.TestCase):
    def test_refund_matched_by_unique_exact_amount(self):
        _, matched, missing = build_deposit(payout(("REFUND FOR CHARGE (CA-CD038407)", -499.0)),
                                            [row("CA-CR004358", -499.0), row("CA-CD038999", 499.0, "Customer Deposit")])
        self.assertEqual(missing, [])
        self.assertEqual([m["LineRefNo"] for m in matched if m.get("LinkedFlag")], ["CA-CR004358"])

    def test_ambiguous_amount_stays_missing(self):
        _, _, missing = build_deposit(payout(("REFUND FOR CHARGE (CA-CD1)", -499.0)),
                                      [row("CA-CR1", -499.0), row("CA-CR2", -499.0)])
        self.assertEqual(missing, ["REFUND FOR CHARGE (CA-CD1)(-499.00)"])


if __name__ == "__main__":
    unittest.main()
