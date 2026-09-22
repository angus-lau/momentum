"""Xoro ERP REST API client.

A small, stdlib-only client shared by the Momentum accounting automations.

Auth/path notes:
  The documented endpoints live under ``/xerp/...`` but that path is gated by
  the web app's forms-auth.  The ``path_prefix`` and ``session_cookie`` knobs
  exist so the integration can switch auth strategy without code changes:
    * ``path_prefix`` - path segment used to reach the API.
    * ``session_cookie`` - value of a ``.ASPXAUTH`` cookie from an authenticated
      browser session, sent on every request when set.

Config lives in ``xoro_config.json`` (non-secret); any session cookie is read
from ``.env`` (``XORO_SESSION_COOKIE``) so nothing sensitive is committed.
"""

import json
import os
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "xoro_config.json")

DEFAULT_BASE_URL = "https://momentum.xoro.one"
DEFAULT_PATH_PREFIX = "Xerp"


# GL endpoint rejects page_size > 100.
GL_PAGE_SIZE = 100


def build_chart_of_accounts(gl_rows):
    """Derive ``{GLCode: {id, name, type}}`` from GL transaction rows.

    There is no dedicated chart-of-accounts endpoint, so we collect the
    distinct accounts referenced by GL transactions. Rows without a GLCode
    are skipped; repeated codes collapse to one entry.
    """
    coa = {}
    for row in gl_rows:
        code = row.get("GLCode")
        if not code:
            continue
        coa[code] = {
            "id": row.get("F_AccountingId"),
            "name": row.get("F_AccountingName"),
            "type": row.get("F_AccountingTypeName"),
        }
    return coa


# Bank statement import formats (BankStatementHeader.ImportTypeId).
IMPORT_TYPE_CSV = 10
IMPORT_TYPE_OFX = 20
IMPORT_TYPE_QIF = 30
IMPORT_TYPE_AUTO = 999


def build_statement(accnt_id, lines, start_date, end_date, end_balance,
                    import_type_id=IMPORT_TYPE_CSV):
    """Build the (header, line_arr) for a bank-statement createstatement call.

    ``lines`` items are friendly dicts: ``date``, ``amount``, ``type``
    (``debit``/``credit``), ``description``, and optional ``payee``,
    ``reference``, ``cheque``. The account id is injected into every line and
    ``EndBalance`` is formatted as a 2-decimal money string, as the API expects.
    """
    header = {
        "ImportTypeId": import_type_id,
        "StartDate": start_date,
        "EndDate": end_date,
        "EndBalance": "%.2f" % float(end_balance),
    }
    line_arr = []
    for ln in lines:
        line_arr.append({
            "AccntId": accnt_id,
            "DeleteFlag": False,
            "Amount": ln["amount"],
            "TypeName": ln["type"],
            "Date": ln["date"],
            "Description": ln.get("description", ""),
            "Payee": ln.get("payee", ""),
            "ChequeNumber": ln.get("cheque", ""),
            "ReferenceNumber": ln.get("reference", ""),
        })
    return header, line_arr


class XoroAPIError(Exception):
    """Raised when the API returns a non-success envelope or HTTP error."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def _env_value(key, env_path=os.path.join(SCRIPT_DIR, ".env")):
    """Read a single value from the project ``.env`` (no dependency on dotenv)."""
    if not os.path.exists(env_path):
        return None
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
    return None


def _http_transport(method, url, headers, body):
    """Default transport: real HTTP via urllib. Returns (status, text)."""
    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class XoroClient:
    def __init__(
        self,
        base_url=DEFAULT_BASE_URL,
        path_prefix=DEFAULT_PATH_PREFIX,
        session_cookie=None,
        transport=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.path_prefix = path_prefix.strip("/")
        self.session_cookie = session_cookie
        self._transport = transport or _http_transport

    @classmethod
    def from_config(cls, config_path=CONFIG_PATH, transport=None):
        """Build a client from ``xoro_config.json`` + ``XORO_SESSION_COOKIE``.

        Missing config falls back to the documented defaults so the client
        works out of the box; the session cookie is read from the environment
        (populated from ``.env``) so nothing secret is committed.
        """
        cfg = {}
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = json.load(f)
        cookie = os.environ.get("XORO_SESSION_COOKIE") or _env_value("XORO_SESSION_COOKIE")
        return cls(
            base_url=cfg.get("base_url", DEFAULT_BASE_URL),
            path_prefix=cfg.get("path_prefix", DEFAULT_PATH_PREFIX),
            session_cookie=cookie,
            transport=transport,
        )

    # ---- request core -------------------------------------------------

    def _headers(self, extra=None):
        headers = {"Accept": "application/json"}
        if self.session_cookie:
            headers["Cookie"] = ".ASPXAUTH=" + self.session_cookie
        if extra:
            headers.update(extra)
        return headers

    def _url(self, endpoint, params=None):
        url = "{}/{}/{}".format(self.base_url, self.path_prefix, endpoint.lstrip("/"))
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    def _unwrap(self, status, text):
        if status != 200:
            raise XoroAPIError("HTTP {}: {}".format(status, text[:200]), code=status)
        payload = json.loads(text)
        if not payload.get("Result", False) or payload.get("ErrorCode", 0) != 0:
            raise XoroAPIError(
                payload.get("Message") or "Xoro API error",
                code=payload.get("ErrorCode"),
            )
        return payload

    def _get(self, endpoint, params=None, *, full=False):
        url = self._url(endpoint, params)
        status, text = self._transport("GET", url, self._headers(), None)
        payload = self._unwrap(status, text)
        return payload if full else payload.get("Data")

    def _paginate(self, endpoint, params, *, page_param="page", extract=None):
        """Fetch every page, concatenating the row lists.

        ``page_param`` is the request field used to select a page (``page`` for
        invoice/credit memo, ``page_number`` for GL). ``extract`` maps a page's
        ``Data`` to its list of rows (defaults to identity for list payloads).
        """
        rows = []
        page = 1
        while True:
            page_params = dict(params)
            page_params[page_param] = page
            payload = self._get(endpoint, page_params, full=True)
            data = payload.get("Data")
            rows.extend(extract(data) if extract else (data or []))
            total = payload.get("TotalPages") or 1
            if page >= total:
                break
            page += 1
        return rows

    def _post(self, endpoint, body, *, full=False):
        url = self._url(endpoint)
        headers = self._headers({"Content-Type": "application/json"})
        status, text = self._transport("POST", url, headers, json.dumps(body))
        payload = self._unwrap(status, text)
        return payload if full else payload.get("Data")

    # ---- read endpoints ----------------------------------------------

    def get_invoices(self, **filters):
        return self._paginate("invoice/getinvoice", filters, page_param="page")

    def get_credit_memos(self, **filters):
        return self._paginate("creditmemo/getcreditmemo", filters, page_param="page")

    def get_item_receipts(self, **filters):
        return self._paginate("bill/getitemreceipt", filters, page_param="page")

    def get_gl_transactions(self, start_date, end_date, *, page_size=GL_PAGE_SIZE, **filters):
        params = dict(filters)
        params.update(
            start_date=start_date, end_date=end_date, page_size=page_size
        )
        return self._paginate(
            "accounting/getgltransactions",
            params,
            page_param="page_number",
            extract=lambda data: (data or {}).get("transactionList", []),
        )

    def get_chart_of_accounts(self, start_date, end_date):
        """Live chart of accounts derived from GL transactions in the window."""
        rows = self.get_gl_transactions(start_date, end_date)
        return build_chart_of_accounts(rows)

    # ---- write endpoints (used by automations, not by the client task) -

    def create_bank_statement(self, header, lines):
        body = {"BankStatementHeader": header, "BankStatementLineArr": lines}
        return self._post("bankdeposit/createstatement", body)

    def import_credit_memo(self, payload):
        return self._post("creditmemo/import", payload)


# ---------- exchange rates ----------

class AmbiguousRate(Exception):
    """More than one rate is equally common that day — the caller must choose."""


def pick_rate(gl_rows, currency_code, home_currency="CAD"):
    """Xoro's exchange rate for ``currency_code`` implied by a day's GL rows.

    Each posting carries ``AmountHomeCurrency / Amount`` — the rate applied when
    that transaction was created — so a day holds several rates and the dominant
    one is Xoro's rate for the date. Rows are restricted to accounts in the target
    currency (an account name ends with "(USD)"), because mixing EUR/GBP postings
    in produces a meaningless average.

    Returns 1 when the currency *is* the home currency. Raises AmbiguousRate when
    the top two counts tie, rather than silently picking one.
    """
    if currency_code == home_currency:
        return 1
    counts = {}
    for r in gl_rows:
        name = r.get("F_AccountingName") or ""
        amount, home = r.get("Amount"), r.get("AmountHomeCurrency")
        if "(%s)" % currency_code not in name or not amount or not home:
            continue
        if abs(amount) <= 1:          # tiny amounts round badly
            continue
        rate = round(home / amount, 5)
        if rate > 0:
            counts[rate] = counts.get(rate, 0) + 1
    if not counts:
        raise AmbiguousRate("no %s postings to derive a rate from" % currency_code)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        raise AmbiguousRate(
            "%s rate is tied that day: %s and %s both appear %d times"
            % (currency_code, ranked[0][0], ranked[1][0], ranked[0][1]))
    return ranked[0][0]


def exchange_rate_for(date, currency_code, client=None, home_currency="CAD"):
    """``pick_rate`` for a ``YYYY-MM-DD`` date, fetching that day's GL."""
    if currency_code == home_currency:
        return 1
    client = client or XoroClient()
    return pick_rate(list(client.get_gl_transactions(date, date)), currency_code, home_currency)
