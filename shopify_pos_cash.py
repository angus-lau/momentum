#!/usr/bin/env python3
"""
Read Shopify POS cash-tracking sessions — the register open/close counts behind
the "Cash - Service Centre" reconciliation.

A POS register session is the cash equivalent of a payout: the till is counted
open, cash sales and refunds run through it, staff make adjustments (drops,
pay-outs), and it is counted closed. Shopify's own figure for whether the till
balanced is ``totalDiscrepancy`` (counted close minus expected close). Those are
exactly the numbers the monthly cash rec needs, so they are read from the API
rather than off the POS report screen.

Usage:
    python3 shopify_pos_cash.py 5120426026          # one session, by id or admin URL
    python3 shopify_pos_cash.py 2026-08             # every session opened that month
    python3 shopify_pos_cash.py 2026-08 --json      # raw, for piping

Location name and the opening/closing staff members are deliberately not read:
they need `read_locations` / `read_users`, and `read_users` additionally requires
a Plus or Advanced plan and a Shopify Support request. `registerName` and the
adjustments carry no staff name either, for the same reason.

SCOPE: needs `read_cash_tracking` on the Admin API token, which is *not* granted
by adding it to the app config alone — the token has to be reissued. Re-run
`python3 shopify_oauth.py _2` after adding the scope, or this exits with the
Shopify access-denied message.
"""

import json
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from shopify_deposits import STORES, _env

API_VERSION = "2025-07"          # cashTrackingSession is not in every version
STORE = "service_center"         # ca-momentumwatch — the POS store

SESSION_FIELDS = """
    id registerName cashTrackingEnabled
    openingTime closingTime openingNote closingNote
    openingBalance { amount currencyCode }
    expectedOpeningBalance { amount currencyCode }
    closingBalance { amount currencyCode }
    expectedClosingBalance { amount currencyCode }
    totalCashSales { amount currencyCode }
    totalCashRefunds { amount currencyCode }
    netCashSales { amount currencyCode }
    totalAdjustments { amount currencyCode }
    totalDiscrepancy { amount currencyCode }
    adjustments(first: 100) {
      edges { node { cash { amount currencyCode } note time } }
    }
    cashTransactions(first: 250) {
      edges { node {
        kind status processedAt
        amountSet { presentmentMoney { amount currencyCode } }
        order { name }
      } }
    }
"""


class ShopifyError(RuntimeError):
    pass


def gql(query, variables=None, store=STORE):
    cfg = STORES[store]
    domain, token = _env(cfg["store_env"]), _env(cfg["token_env"])
    if not domain or not token:
        raise ShopifyError("missing Shopify creds for %r (set %s / %s in .env)"
                           % (store, cfg["store_env"], cfg["token_env"]))
    req = urllib.request.Request(
        "https://%s/admin/api/%s/graphql.json" % (domain, API_VERSION),
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    if data.get("errors"):
        first = data["errors"][0]
        need = (first.get("extensions") or {}).get("requiredAccess")
        if need:
            raise ShopifyError(
                "Shopify refused the query: %s\n"
                "Add the scope to the app config, then REISSUE the token:\n"
                "    python3 shopify_oauth.py _2" % first["message"])
        raise ShopifyError("; ".join(e.get("message", str(e)) for e in data["errors"]))
    return data["data"]


def session_id(value):
    """Accept a bare id, a gid, or the admin register-session URL."""
    value = str(value)
    # prefer the URL's own segment, so a store handle containing digits can't win
    m = (re.search(r"register-sessions/(\d+)", value)
         or re.search(r"CashTrackingSession/(\d+)", value)
         or re.fullmatch(r"\s*(\d{6,})\s*", value))
    if not m:
        raise ValueError("no session id in %r" % (value,))
    return "gid://shopify/CashTrackingSession/%s" % m.group(1)


def get_session(value, store=STORE):
    sid = session_id(value)
    data = gql("query($id: ID!) { cashTrackingSession(id: $id) { %s } }" % SESSION_FIELDS,
               {"id": sid}, store)
    s = data.get("cashTrackingSession")
    if not s:
        raise ShopifyError("no session %s on this store" % sid)
    return s


def shop_timezone(store=STORE):
    """The store's IANA timezone.

    Needed because the API returns session times in UTC but the search index
    filters them in *shop-local* time, and because a month's register session is
    the one that CLOSES in that month — the register is closed and reopened at
    month end, so the session covering August opens on July 31 in the evening
    local time (just after midnight UTC on August 1).
    """
    return gql("{ shop { ianaTimezone } }", store=store)["shop"]["ianaTimezone"]


def _local(ts, tz):
    """An API timestamp ("...Z") as a date in the shop's timezone."""
    if not ts:
        return None
    return (datetime.fromisoformat(ts.replace("Z", "+00:00"))
            .astimezone(ZoneInfo(tz)).date())


def month_sessions(month, store=STORE):
    """Every session belonging to ``month`` ("YYYY-MM").

    A session belongs to the month it CLOSED in, local time — that close is the
    month-end count the reconciliation is built from. A session still open is
    keyed on its opening month instead, so the current month shows its live till.
    """
    year, mon = (int(x) for x in month.split("-"))
    tz = shop_timezone(store)
    # The index reads these bounds as shop-local dates; pad both ends so a session
    # that opens the evening before the 1st, or closes just after the last day,
    # still comes back for the client-side check below.
    start = date(year, mon, 1) - timedelta(days=2)
    end = date(year + (mon == 12), mon % 12 + 1, 1) + timedelta(days=2)
    q = ("query($q: String!, $after: String) {"
         " cashTrackingSessions(first: 50, after: $after, query: $q,"
         "   sortKey: OPENING_TIME_ASC) {"
         "   pageInfo { hasNextPage endCursor }"
         "   edges { node { %s } } } }" % SESSION_FIELDS)
    found, after = [], None
    while True:
        data = gql(q, {"q": "opening_time:>=%s opening_time:<%s" % (start, end), "after": after},
                   store)
        conn = data["cashTrackingSessions"]
        found.extend(e["node"] for e in conn["edges"])
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]
    out = []
    for s in found:
        key = _local(s.get("closingTime"), tz) or _local(s.get("openingTime"), tz)
        if key and (key.year, key.month) == (year, mon):
            out.append(s)
    return out


def _money(m):
    return Decimal(m["amount"]) if m and m.get("amount") is not None else None


def _fmt(m):
    v = _money(m)
    return "        —" if v is None else "%9s" % ("%.2f" % v)


def balances(s):
    """Check the session's arithmetic, and isolate the month's own cash variance.

    Shopify's ``expectedClosingBalance`` is built from the balance actually
    COUNTED at open, plus the cash that moved:

        expected closing = opening counted + net cash sales + total adjustments

    The month's real variance is then ``closing counted - expected closing``.

    ``totalDiscrepancy`` is NOT always that figure. When the opening count differs
    from what the previous close predicted, Shopify folds that prior-period
    surprise into it too — the Dec 2025 session opened at 2514.50 against an
    expected 0.00 ("Did not remove cash") and reports a 2513.30 discrepancy, which
    is that 2514.50 less the month's own -1.20. Taking ``totalDiscrepancy`` as the
    month's variance would therefore put a prior-period correction into this
    month's statement, so ``variance`` below is computed, not read.

    Returns ``(ok, variance, carried_in)``, all ``None`` for a session still open:
      ok         -- the expected-closing identity holds
      variance   -- the month's own over/short, i.e. the statement's variance line
      carried_in -- opening counted less opening expected; non-zero means the
                    previous period was closed on a wrong count and needs its own
                    correction rather than being buried in this month's figure
    """
    opening = _money(s.get("openingBalance"))
    expected_close = _money(s.get("expectedClosingBalance"))
    closing = _money(s.get("closingBalance"))
    if s.get("closingTime") is None or None in (opening, expected_close, closing):
        return None, None, None
    moved = sum(filter(None, (_money(s.get("netCashSales")),
                              _money(s.get("totalAdjustments")))))
    ok = (opening + moved) == expected_close
    variance = closing - expected_close
    expected_open = _money(s.get("expectedOpeningBalance"))
    carried_in = None if expected_open is None else opening - expected_open
    return ok, variance, carried_in


def describe(s):
    """One session as the rec needs to read it."""
    lines = []
    lines.append("Session %s — register %s" % (s["id"].rsplit("/", 1)[-1], s.get("registerName") or "?"))
    lines.append("  opened  %s" % s.get("openingTime"))
    lines.append("  closed  %s" % (s.get("closingTime") or "(still open)"))
    for label, key in (("opening balance counted", "openingBalance"),
                       ("opening balance expected", "expectedOpeningBalance"),
                       ("cash sales", "totalCashSales"),
                       ("cash refunds", "totalCashRefunds"),
                       ("net cash sales", "netCashSales"),
                       ("adjustments", "totalAdjustments"),
                       ("closing balance expected", "expectedClosingBalance"),
                       ("closing balance counted", "closingBalance"),
                       ("DISCREPANCY", "totalDiscrepancy")):
        lines.append("  %-25s %s" % (label, _fmt(s.get(key))))
    for note_key, label in (("openingNote", "opening note"), ("closingNote", "closing note")):
        if s.get(note_key):
            lines.append("  %-25s %s" % (label, s[note_key]))
    adj = [e["node"] for e in ((s.get("adjustments") or {}).get("edges") or [])]
    if adj:
        lines.append("  adjustments:")
        for a in adj:
            lines.append("    %s %9s  %s" % (
                (a.get("time") or "")[:19], "%.2f" % _money(a.get("cash")), a.get("note") or ""))
    ok, variance, carried_in = balances(s)
    if ok is not None:
        lines.append("  %-25s %s" % (
            "check" if ok else "CHECK FAILED",
            "opening counted + net sales + adjustments = expected closing"
            if ok else "expected closing disagrees with the movements"))
        lines.append("  %-25s %9s   <- the month's own over/short" % ("variance", "%.2f" % variance))
        if carried_in:
            lines.append("  %-25s %9s   <- PRIOR PERIOD: opening count differed from the last close;"
                         % ("carried in", "%.2f" % carried_in))
            lines.append("  %-25s %9s      correct that separately, it is inside totalDiscrepancy"
                         % ("", ""))
    txns = [e["node"] for e in ((s.get("cashTransactions") or {}).get("edges") or [])]
    if txns:
        lines.append("  cash transactions (%d):" % len(txns))
        for t in txns:
            money = ((t.get("amountSet") or {}).get("presentmentMoney") or {})
            lines.append("    %s %-10s %9s  %s" % (
                (t.get("processedAt") or "")[:19], t.get("kind", "").lower(),
                money.get("amount") or "?", (t.get("order") or {}).get("name") or ""))
    return "\n".join(lines)


def main(argv):
    as_json = "--json" in argv
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 1
    target = args[0]
    try:
        if re.fullmatch(r"\d{4}-\d{2}", target):
            sessions = month_sessions(target)
        else:
            sessions = [get_session(target)]
    except ShopifyError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(sessions, indent=2))
        return 0
    if not sessions:
        print("no cash-tracking sessions for %s" % target)
        return 0
    for s in sessions:
        print(describe(s))
        print()
    total = sum((_money(s.get("netCashSales")) or Decimal(0)) for s in sessions)
    disc = sum((_money(s.get("totalDiscrepancy")) or Decimal(0)) for s in sessions)
    print("%d session(s): net cash sales %.2f, total discrepancy %.2f" % (len(sessions), total, disc))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
