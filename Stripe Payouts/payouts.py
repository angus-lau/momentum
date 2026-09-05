#!/usr/bin/env python3
"""
Print Stripe payouts and their underlying balance transactions for a date range.

Usage:
    python3 payouts.py                      # today
    python3 payouts.py 2026-04-16           # single day
    python3 payouts.py 2026-04-01 2026-04-30   # range (inclusive)

Reads STRIPE_LIVE_KEY from a .env file in this folder or any parent folder.
"""

import sys
import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ---------- .env loading (stdlib only) ----------

def load_env():
    """Walk up from this file looking for a .env file and read its keys into os.environ."""
    for d in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        env = d / ".env"
        if env.is_file():
            for line in env.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return env
    return None


# ---------- Stripe API ----------

def stripe_get(path, params=None):
    qs = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"https://api.stripe.com/v1{path}" + (f"?{qs}" if qs else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {KEY}"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        sys.exit(f"Stripe API error {e.code}: {body}")


# ---------- Formatting ----------

def money(cents, currency):
    sign = "-" if cents < 0 else " "
    return f"{sign}{abs(cents)/100:>10,.2f} {currency.upper()}"


def parse_date(s):
    """Accept YYYY-MM-DD."""
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


# ---------- Main ----------

def main(argv):
    args = argv[1:]
    if len(args) == 0:
        start = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif len(args) == 1:
        start = parse_date(args[0])
        end = start + timedelta(days=1)
    elif len(args) == 2:
        start = parse_date(args[0])
        end = parse_date(args[1]) + timedelta(days=1)  # inclusive end date
    else:
        sys.exit(__doc__)

    label = (start.strftime("%Y-%m-%d") if (end - start) == timedelta(days=1)
             else f"{start.strftime('%Y-%m-%d')} → {(end - timedelta(days=1)).strftime('%Y-%m-%d')}")

    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    # Try arrival_date first (what landed in the bank), fall back to created
    resp = stripe_get("/payouts", {
        "arrival_date[gte]": start_ts,
        "arrival_date[lt]":  end_ts,
        "limit": 100,
    })
    payouts = resp.get("data", [])
    via = "arrival_date"
    if not payouts:
        resp = stripe_get("/payouts", {
            "created[gte]": start_ts,
            "created[lt]":  end_ts,
            "limit": 100,
        })
        payouts = resp.get("data", [])
        via = "created"

    print(f"Stripe payouts for {label}  ({len(payouts)} found via {via})")

    if not payouts:
        return

    grand_amount = 0
    grand_fee = 0
    for po in payouts:
        arr = datetime.fromtimestamp(po["arrival_date"], tz=timezone.utc).strftime("%Y-%m-%d")
        cre = datetime.fromtimestamp(po["created"],      tz=timezone.utc).strftime("%Y-%m-%d")
        print()
        print(f"=== {po['id']}")
        print(f"    arrival {arr} | created {cre} | {money(po['amount'], po['currency'])} | "
              f"status {po['status']} | dest {po.get('destination','')}")
        print(f"    {'type':<22} {'amount':>14} {'fee':>10} {'net':>14}  description")
        print(f"    {'-'*22} {'-'*14} {'-'*10} {'-'*14}  {'-'*40}")

        starting_after = None
        line_count = 0
        po_fee_sum = 0
        while True:
            params = {"payout": po["id"], "limit": 100}
            if starting_after:
                params["starting_after"] = starting_after
            bt = stripe_get("/balance_transactions", params)
            for t in bt["data"]:
                desc = (t.get("description") or "").replace("\n", " ")[:60]
                print(f"    {t['type']:<22} {money(t['amount'], t['currency']):>14} "
                      f"{t['fee']/100:>10,.2f} {money(t['net'], t['currency']):>14}  {desc}")
                po_fee_sum += t["fee"]
                line_count += 1
            if not bt.get("has_more"):
                break
            starting_after = bt["data"][-1]["id"]
        print(f"    ({line_count} lines, fees ${po_fee_sum/100:,.2f})")
        grand_amount += po["amount"]
        grand_fee += po_fee_sum

    if len(payouts) > 1:
        print()
        print(f"TOTAL across {len(payouts)} payouts: {grand_amount/100:,.2f} | total fees: ${grand_fee/100:,.2f}")


if __name__ == "__main__":
    env_path = load_env()
    KEY = os.environ.get("STRIPE_LIVE_KEY")
    if not KEY:
        sys.exit("Missing STRIPE_LIVE_KEY. Add it to .env at the project root or a parent folder.")
    main(sys.argv)
