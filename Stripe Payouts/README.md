# Stripe Payouts

Print Stripe payouts and the underlying balance transactions (charges, refunds, fees, adjustments) for any date or range. Output is terminal-only; no files written.

## Usage

```sh
# today
python3 payouts.py

# one day
python3 payouts.py 2026-04-16

# range (inclusive)
python3 payouts.py 2026-04-01 2026-04-30
```

## Setup

API key is read from `.env` at the project root (`/Users/angus/Documents/Projects/momentum/.env`). The script walks up the folder tree to find it, so it works whether you run from this folder or the project root.

Key name: `STRIPE_LIVE_KEY`. Use a **restricted key** (`rk_live_…`) with Read access on Balance, Charges, Refunds, Payouts. Never put `sk_live_…` keys in this file unless absolutely necessary.

No `pip install` needed — the script is stdlib only.

## Output shape

```
Stripe payouts for 2026-04-16  (1 found via arrival_date)

=== po_1TMHRzD0MKndqzPlnyWwj3c7
    arrival 2026-04-16 | created 2026-04-15 |      439.84 CAD | status paid | dest ba_…
    type                           amount        fee            net  description
    ---------------------- -------------- ---------- --------------  ----------------------------------------
    payout                 -    439.84 CAD       0.00 -    439.84 CAD  STRIPE PAYOUT
    charge                      278.25 CAD       8.37      269.88 CAD  CA-CD034793
    charge                      175.35 CAD       5.39      169.96 CAD  CA-CD034790
    (3 lines, fees $13.76)
```

The `description` on each charge line is the Xoro reference (e.g. `CA-CD034793`) — that's the join key if you want to reconcile against Xoro customer-deposit records.

## Date filtering

By default the script filters by `arrival_date` (when the money landed in your bank), which is what you want for bank reconciliation. If no payouts are found, it retries with `created` (when Stripe initiated the payout). Both are queried in UTC.
