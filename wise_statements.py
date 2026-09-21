"""Pull Wise balance statements (PDF + CSV) for a given month, skipping empty ones.

Uses the Wise API (api.wise.com) with a personal access token (``WISE_API_KEY``
in ``.env``, from Wise > Settings > API tokens). Only pulls PDF/CSV for a
balance if its compact JSON statement shows at least one transaction in the
requested month — avoids littering the reconciliation folders with empty
statements for currencies that saw no activity that month.

Saves straight into the OneDrive bank-reconciliation tree, matching the
existing "Wise CAD" / "Wise EUR" / "Wise GBP" / "26 0M" folder + filename
convention used there already: reuses the month folder if one exists for
that currency, otherwise creates it.

    python3 wise_statements.py 2026-07                  # CAD + EUR + GBP
    python3 wise_statements.py 2026-07 --currency GBP    # just GBP

Business profile id (ST. MORITZ WATCH CORP) is resolved automatically from
GET /v2/profiles — first BUSINESS profile on the token wins.
"""

import argparse
import calendar
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")

API_BASE = "https://api.wise.com"

ONEDRIVE_BASE = (
    "/Users/angus/Library/CloudStorage/OneDrive-St.MoritzWatch/"
    "Accounting Docs/Bank Reconciliations"
)
FISCAL_YEAR_END_MONTH = 7  # FY ends July 31: 2026-08 is the first month of FY2027


def fiscal_year_folder(year, month):
    """``FY2027`` for 2026-08 .. 2027-07."""
    return f"FY{year + 1 if month > FISCAL_YEAR_END_MONTH else year}"

# Only the Wise currencies actually reconciled (each has a OneDrive folder).
CURRENCY_FOLDER = {"CAD": "Wise CAD", "EUR": "Wise EUR", "GBP": "Wise GBP"}


def _env(key, env_path=ENV_PATH):
    if not os.path.exists(env_path):
        return None
    for line in open(env_path):
        line = line.strip()
        if line.startswith(key + "="):
            return line.partition("=")[2].strip().strip('"').strip("'")
    return None


def _get(path, token, params=None, accept=None):
    url = API_BASE + path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    if accept:
        req.add_header("Accept", accept)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Wise API {path} -> {e.code}: {e.read().decode()[:500]}") from e


def _business_profile_id(token):
    profiles = json.loads(_get("/v2/profiles", token))
    for p in profiles:
        if p.get("type") == "BUSINESS":
            return p["id"]
    raise RuntimeError("no BUSINESS profile found on this Wise API token")


def _balances(token, profile_id):
    return json.loads(_get(f"/v4/profiles/{profile_id}/balances", token, {"types": "STANDARD"}))


def _month_bounds(month_str):
    year, month = (int(x) for x in month_str.split("-"))
    start = datetime(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59)
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    return start.strftime(fmt), end.strftime(fmt), start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _month_folder_name(month_str):
    year, month = month_str.split("-")
    return f"{year[2:]} {month}"


def _statement_count(token, profile_id, balance_id, currency, interval_start, interval_end):
    body = json.loads(_get(
        f"/v1/profiles/{profile_id}/balance-statements/{balance_id}/statement.json",
        token,
        {
            "currency": currency,
            "intervalStart": interval_start,
            "intervalEnd": interval_end,
            "type": "COMPACT",
        },
    ))
    return len(body.get("transactions", []))


def _download_statement(token, profile_id, balance_id, currency, interval_start, interval_end, fmt, out_dir, start_date, end_date):
    data = _get(
        f"/v1/profiles/{profile_id}/balance-statements/{balance_id}/statement.{fmt}",
        token,
        {
            "currency": currency,
            "intervalStart": interval_start,
            "intervalEnd": interval_end,
            "type": "COMPACT",
        },
    )
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"statement_{balance_id}_{currency}_{start_date}_{end_date}.{fmt}")
    with open(path, "wb") as f:
        f.write(data)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("month", help="Month to pull, e.g. 2026-07")
    parser.add_argument("--currency", help="Only check this currency (e.g. GBP); default: CAD, EUR, GBP")
    args = parser.parse_args()

    token = os.environ.get("WISE_API_KEY") or _env("WISE_API_KEY")
    if not token:
        sys.exit("missing WISE_API_KEY in .env")

    interval_start, interval_end, start_date, end_date = _month_bounds(args.month)
    month_folder = _month_folder_name(args.month)

    profile_id = _business_profile_id(token)
    balances = _balances(token, profile_id)

    wanted_currencies = [args.currency.upper()] if args.currency else list(CURRENCY_FOLDER)
    balances = [b for b in balances if b["currency"] in wanted_currencies]
    if not balances:
        sys.exit(f"no matching Wise balance for currency filter {wanted_currencies}")

    any_activity = False
    for bal in balances:
        currency = bal["currency"]
        count = _statement_count(token, profile_id, bal["id"], currency, interval_start, interval_end)
        if count == 0:
            print(f"{currency}: no transactions in {args.month} — skipping")
            continue
        any_activity = True

        folder = CURRENCY_FOLDER.get(currency)
        if not folder:
            print(f"{currency}: {count} transaction(s) in {args.month}, but no OneDrive folder mapped — skipping download")
            continue
        out_dir = os.path.join(ONEDRIVE_BASE, fiscal_year_folder(start_date.year, start_date.month), folder, month_folder)
        existed = os.path.isdir(out_dir)
        print(f"{currency}: {count} transaction(s) in {args.month} — {'adding to' if existed else 'creating'} \"{folder}/{month_folder}\"")
        for fmt in ("pdf", "csv"):
            path = _download_statement(token, profile_id, bal["id"], currency, interval_start, interval_end, fmt, out_dir, start_date, end_date)
            print(f"  -> {path}")

    if not any_activity:
        print(f"No Wise balance had activity in {args.month}; nothing downloaded.")


if __name__ == "__main__":
    main()
