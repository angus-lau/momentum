#!/usr/bin/env python3
"""
Parse an Afterpay settlement export (Business Hub -> Reconciliation -> Settlement Export)
and summarise it by settlement date so each date ties to a bank deposit.

Usage:
    python3 afterpay_reconcile.py /path/to/settlements-xxxx.csv

Outputs (written next to the input file):
    afterpay_reconcile_lines.csv    cleaned one-row-per-transaction view
    afterpay_settlements_summary.csv  one row per settlement date (= one deposit)

Cross-referencing each line to its Shopify order is a separate step (needs the
Shopify Admin API); this script keeps the join keys (Afterpay Order ID and
Merchant Order ID) in its output so that step can match against them.

Stdlib only.
"""

import sys
import csv
import pathlib
from collections import defaultdict


def money(s):
    """'$266.145000' / '$1,259.72' / '' -> float."""
    if s is None:
        return 0.0
    s = str(s).strip().replace("$", "").replace(",", "")
    if s == "":
        return 0.0
    return float(s)


# Columns we carry through to the cleaned line output (in order).
LINE_COLUMNS = [
    "Settlement Date",
    "Order Date and Time",
    "Type",
    "Afterpay Order ID",
    "Merchant Order ID",
    "Consumer Country",
    "Order Amount",
    "Merchant Fee incl Tax",
    "Net Settlement Amount",
    "Afterpay Refund ID",
    "Merchant Refund ID",
]


def main(path):
    path = pathlib.Path(path).expanduser()
    if not path.is_file():
        print(f"Not a file: {path}", file=sys.stderr)
        sys.exit(2)

    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))

    rows = [r for r in rows if (r.get("Afterpay Order ID") or "").strip()]
    if not rows:
        print("No transaction rows found.", file=sys.stderr)
        sys.exit(2)

    # Per settlement date: each date is one payout to the bank.
    by_date = defaultdict(lambda: {"count": 0, "gross": 0.0, "fee": 0.0, "net": 0.0})
    for r in rows:
        d = by_date[r["Settlement Date"]]
        d["count"] += 1
        d["gross"] += money(r.get("Order Amount"))
        d["fee"] += money(r.get("Merchant Fee incl Tax"))
        d["net"] += money(r.get("Net Settlement Amount"))

    out_dir = path.parent

    # Cleaned line view
    out_lines = out_dir / "afterpay_reconcile_lines.csv"
    with open(out_lines, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LINE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in LINE_COLUMNS})

    # Per-settlement-date summary
    out_summary = out_dir / "afterpay_settlements_summary.csv"
    with open(out_summary, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Settlement Date", "Transactions", "Gross", "Merchant Fee",
                    "Net (raw)", "Net (rounded, = deposit)"])
        for d in sorted(by_date):
            v = by_date[d]
            w.writerow([d, v["count"], f"{v['gross']:.2f}", f"{v['fee']:.4f}",
                        f"{v['net']:.4f}", f"{round(v['net'], 2):.2f}"])

    # Terminal summary
    g_gross = sum(v["gross"] for v in by_date.values())
    g_fee = sum(v["fee"] for v in by_date.values())
    g_net = sum(v["net"] for v in by_date.values())
    print(f"Parsed {len(rows)} transaction(s) across {len(by_date)} settlement date(s)\n")
    print(f"{'Settlement Date':<16}{'Txns':>5}{'Gross':>12}{'Fee':>12}{'Net (deposit)':>16}")
    for d in sorted(by_date):
        v = by_date[d]
        print(f"{d:<16}{v['count']:>5}{v['gross']:>12,.2f}{v['fee']:>12,.4f}{round(v['net'],2):>16,.2f}")
    print(f"{'-'*61}")
    print(f"{'TOTAL':<16}{len(rows):>5}{g_gross:>12,.2f}{g_fee:>12,.4f}{round(g_net,2):>16,.2f}")
    print(f"\n  -> {out_lines.name}")
    print(f"  -> {out_summary.name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1])
