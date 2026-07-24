"""Self-healing client for Xoro's internal ASP.NET ScriptService API.

The Xoro UI runs on ``/WebServices/{Service}.asmx/{method}`` endpoints (POST
JSON of named params, response ``{"d": ...}``), authenticated by the session
cookie. This client wraps that surface and, when a call bounces to the login
page (expired cookie), transparently re-logs-in via ``xoro_login.login()`` and
retries once — so callers never deal with auth.

Used by the reconciliation flow: ``getBankReconcileAccountList``,
``getBankReconciledTransactions``, ``saveJournalEntry``, ``finishBankRec``, etc.
"""

import csv
import json
import os
import urllib.request
from decimal import Decimal

import xoro_login

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "xoro_config.json")
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")

DEFAULT_BASE_URL = "https://momentum.xoro.one"
LOGIN_MARKER = "login.aspx"

# Bank-statement upload lives on this service (NOT bankdeposit / BankStatement).
# Discovered 2026-07-19 by capturing the real UploadBankStatement.aspx UI flow.
BANK_STMT_SERVICE = "ConnectBankWebMethods"
IMPORT_TYPE_CSV = 10   # ImportTypeId values: CSV=10, OFX=20, QIF=30, AUTO=999

# Bank reconciliation (the "Reconcile Now" -> start flow) lives here.
# Discovered 2026-07-19 by reading BankRec.aspx's addBankRecHeader call.
BANK_RECONCILE_SERVICE = "BankReconcileWebMethods"

# Bank deposits (create from undeposited payments). Cracked 2026-07-24 by
# capturing a real BankDeposit.aspx save.
BANK_DEPOSIT_SERVICE = "BankDepositWebMethods"


class WebMethodError(Exception):
    """Raised when an .asmx call fails (HTTP error, or auth that won't refresh)."""


def _find_dict_list(obj, key):
    """Recursively find the first list of dicts whose items contain ``key``."""
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and key in obj[0]:
            return obj
        for x in obj:
            found = _find_dict_list(x, key)
            if found:
                return found
    elif isinstance(obj, dict):
        for v in obj.values():
            found = _find_dict_list(v, key)
            if found:
                return found
    return []


def _fmt_stmt_amount(amount):
    """Format a statement-line amount the way Xoro's parser emits it.

    Xoro sends amounts as strings with trailing zeros trimmed (``-1.00`` -> ``-1``,
    ``-401.90`` -> ``-401.9``); negative = debit/withdrawal, positive = deposit.
    A string is passed through untouched so exotic values can be forced.
    """
    if isinstance(amount, str):
        return amount
    s = "%.2f" % float(amount)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _fmt_stmt_date(date):
    """Format a date as ``M/D/YYYY`` (no leading zeros), Xoro's line-date shape.

    Accepts a ``date``/``datetime``, an ISO ``YYYY-MM-DD`` string, or a
    ``MM/DD/YYYY`` / ``M/D/YYYY`` string (leading zeros stripped).
    """
    if isinstance(date, str):
        if "-" in date:                       # ISO YYYY-MM-DD
            y, m, d = date.split("-")
        elif "/" in date:                     # US MM/DD/YYYY (or M/D/YYYY)
            m, d, y = date.split("/")
        else:
            return date
        return "%d/%d/%d" % (int(m), int(d), int(y))
    return "%d/%d/%d" % (date.month, date.day, date.year)


def activity_rows_to_lines(rows, *, credit_card=False):
    """Map raw activity rows (``Date``, ``Description``, ``Amount``) to statement lines.

    ``rows`` is an iterable of dicts (e.g. a ``csv.DictReader``). ``Description``
    fills both ``payee`` and ``description`` (Xoro's statements duplicate them).

    **Credit cards:** pass ``credit_card=True`` to multiply every amount by −1.
    A raw card statement shows charges as positive and payments as negative, but
    the bank-statement import wants charges negative / payments positive. Leave it
    ``False`` for chequing/bank exports, which already use the right sign.
    """
    sign = Decimal(-1) if credit_card else Decimal(1)
    lines = []
    for r in rows:
        desc = (r.get("Description") or "").strip()
        amount = Decimal(str(r["Amount"]).strip()) * sign
        lines.append({
            "date": (r["Date"] or "").strip(),
            "amount": amount,
            "payee": desc,
            "description": desc,
        })
    return lines


def load_activity_csv(path, *, credit_card=False):
    """Read an activity CSV (``Date,Description,Amount``) into statement lines.

    See :func:`activity_rows_to_lines` for the ``credit_card`` sign rule.
    """
    with open(path, newline="") as f:
        return activity_rows_to_lines(csv.DictReader(f), credit_card=credit_card)


def build_bank_statement(faccount_id, lines, *, import_type_id=IMPORT_TYPE_CSV,
                         start_date=None, end_date=None,
                         start_balance=None, end_balance=None):
    """Assemble the ``bankStmtData`` object for ``uploadBankStatementManual``.

    ``faccount_id`` is the bank account's Xoro ``FAccountId`` (from the account
    dropdown / ``get_bank_statement_accounts``). ``lines`` are friendly dicts:
    required ``date`` and ``amount``; optional ``payee``, ``description``,
    ``reference``, ``cheque``, ``allow_duplicate``.

    Returns the dict shape captured from the real UI. Note the caller must
    JSON-*stringify* this before sending (see ``create_bank_statement``): the
    ScriptService param ``bankStmtData`` is a double-encoded JSON string.
    """
    header = {
        "ImportTypeId": import_type_id,
        "StartDate": start_date,
        "EndDate": end_date,
        "StartBalance": start_balance,
        "EndBalance": end_balance,
    }
    line_arr = []
    for i, ln in enumerate(lines, start=1):
        line_arr.append({
            "AccntId": faccount_id,
            "AllowDuplicate": ln.get("allow_duplicate"),
            "Amount": _fmt_stmt_amount(ln["amount"]),
            "ChequeNumber": ln.get("cheque"),
            "Date": _fmt_stmt_date(ln["date"]),
            "Description": ln.get("description", ""),
            "ErrorText": None,
            "HasError": False,
            "IsDuplicate": False,
            "Payee": ln.get("payee", ""),
            "Reference": ln.get("reference", ""),
            "Seq": i,
        })
    return {"BankStatementHeader": header, "BankStatementLineArr": line_arr}


def _http_transport(method, url, headers, body):
    """Real HTTP via urllib, WITHOUT following redirects.

    A 3xx to the login page is the expired-cookie signal, so we must see it
    rather than transparently follow it to a 200 login page. Returns
    (status, location-or-body).
    """
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307):
            return e.code, e.headers.get("Location", "")
        return e.code, e.read().decode("utf-8", "replace")


def _read_env_cookie():
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH):
            if line.startswith("XORO_COOKIE="):
                return line.partition("=")[2].strip()
    return None


class WebMethodClient:
    def __init__(self, base_url=DEFAULT_BASE_URL, cookie=None, login_fn=None, transport=None):
        self.base_url = base_url.rstrip("/")
        self.cookie = cookie
        self._login = login_fn or xoro_login.login
        self._transport = transport or _http_transport

    @classmethod
    def from_config(cls, config_path=CONFIG_PATH, transport=None):
        base = DEFAULT_BASE_URL
        if os.path.exists(config_path):
            base = json.load(open(config_path)).get("base_url", DEFAULT_BASE_URL)
        return cls(base_url=base, cookie=_read_env_cookie(), transport=transport)

    def _url(self, service, method):
        return "{}/WebServices/{}.asmx/{}".format(self.base_url, service, method)

    def _headers(self):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    @staticmethod
    def _is_login_bounce(status, text):
        return status in (301, 302, 303, 307) and LOGIN_MARKER in (text or "")

    def call(self, service, method, **params):
        """Call an .asmx method, returning the unwrapped ``d`` payload."""
        body = json.dumps(params)
        url = self._url(service, method)

        for attempt in range(2):
            status, text = self._transport("POST", url, self._headers(), body)
            if self._is_login_bounce(status, text):
                if attempt == 1:
                    raise WebMethodError(
                        "session expired and re-login did not restore access: %s/%s"
                        % (service, method)
                    )
                self.cookie = self._login()  # refresh and retry once
                continue
            if status != 200:
                raise WebMethodError(
                    "%s/%s -> HTTP %s: %s" % (service, method, status, (text or "")[:200])
                )
            return self._unwrap(text)
        raise WebMethodError("unreachable")

    @staticmethod
    def _unwrap(text):
        payload = json.loads(text)
        d = payload.get("d")
        if isinstance(d, str):
            try:
                return json.loads(d)
            except (ValueError, TypeError):
                return d
        return d

    # ---- bank-statement upload (ConnectBankWebMethods) ----------------

    def get_bank_statement_accounts(self):
        """Return the bank accounts eligible for statement upload.

        Each item carries the ``FAccountId`` used as a line's ``AccntId``.
        """
        data = self.call(BANK_STMT_SERVICE, "getDataForBankStmtUpload")
        return (data or {}).get("Data", {}).get("BankAccountList", [])

    def get_last_bank_transaction(self, faccount_id):
        """Last statement transaction for an account — a dedup/cursor helper."""
        data = self.call(
            BANK_STMT_SERVICE, "getLastBankTransactionFromFAccountId",
            fAccountId=faccount_id,
        )
        return (data or {}).get("Data") if isinstance(data, dict) else data

    def create_bank_statement(self, faccount_id, lines, **header_opts):
        """Create a bank statement from ``lines`` (see ``build_bank_statement``).

        Posts to ``ConnectBankWebMethods/uploadBankStatementManual`` with
        ``bankStmtData`` as a JSON-stringified string (double-encoded, as the
        real UI sends it). Returns the parsed Xoro envelope; raises
        ``WebMethodError`` if ``Result`` is not truthy.
        """
        payload = build_bank_statement(faccount_id, lines, **header_opts)
        envelope = self.call(
            BANK_STMT_SERVICE, "uploadBankStatementManual",
            bankStmtData=json.dumps(payload),
        )
        if not isinstance(envelope, dict) or not envelope.get("Result"):
            msg = envelope.get("Message") if isinstance(envelope, dict) else envelope
            raise WebMethodError("uploadBankStatementManual failed: %s" % msg)
        return envelope

    # ---- reconciliation setup (BankReconcileWebMethods) ---------------

    def get_last_reconcile_header(self, faccount_id):
        """Return the account's last reconcile header (source of the beginning balance).

        The ``Data`` includes ``beginningBal`` (which auto-carries into the next
        reconciliation) and ``lastStatementDate``.
        """
        env = self.call(
            BANK_RECONCILE_SERVICE, "getLastReconcileHeaderDetailsFromAccountId",
            bnkrcAccntId=faccount_id,
        )
        return (env or {}).get("Data") if isinstance(env, dict) else env

    def _currency_id_for_account(self, faccount_id):
        for a in self.get_bank_statement_accounts():
            if a.get("FAccountingId") == faccount_id:
                return a.get("CurrencyId")
        return None

    def start_reconciliation(self, faccount_id, ending_balance, ending_date,
                             *, currency_id=None, beginning_balance=None):
        """Start a bank reconciliation, setting its ending balance + statement date.

        Mirrors the Reconcile Centre's "Reconcile Now" -> start: a single
        ``addBankRecHeader`` call that creates the reconciliation. Only the setup
        is automated — line matching stays manual.

        The **beginning balance auto-carries** from the prior reconciliation and is
        read here when not supplied; likewise ``currency_id`` is resolved from the
        account. ``ending_balance``/``ending_date`` map to the statement's
        ``EndBalance``/``EndDate`` (for a credit card, ``ending_balance`` is already
        negated by the statement pipeline).

        Returns the created reconcile header (``Data``); raises ``WebMethodError``
        if ``Result`` is not truthy. NB ``brHeaderObj`` is a JSON-stringified string
        (double-encoded), like ``bankStmtData``.
        """
        if beginning_balance is None:
            last = self.get_last_reconcile_header(faccount_id)
            beginning_balance = (last or {}).get("beginningBal") if isinstance(last, dict) else None
        if currency_id is None:
            currency_id = self._currency_id_for_account(faccount_id)
        header = {
            "AccountId": faccount_id,
            "CurrencyId": currency_id,
            "AccountEndBal": float(ending_balance),
            "LastStatementDate": ending_date,
            "AccountBegBal": beginning_balance,
        }
        envelope = self.call(
            BANK_RECONCILE_SERVICE, "addBankRecHeader",
            brHeaderObj=json.dumps(header),
        )
        if not isinstance(envelope, dict) or not envelope.get("Result"):
            msg = envelope.get("Message") if isinstance(envelope, dict) else envelope
            raise WebMethodError("addBankRecHeader failed: %s" % msg)
        return envelope.get("Data", envelope)

    def void_reconciliation(self, bank_rec_header_id):
        """Void/delete a reconciliation (reverts ending balance to the prior one)."""
        return self.call(
            BANK_RECONCILE_SERVICE, "voidBankRec", bankRecHeaderId=bank_rec_header_id,
        )

    # ---- bank deposits (BankDepositWebMethods) ------------------------

    def get_undeposited_transactions(self, currency_id, *, size=3000):
        """Undeposited payment rows for a currency (each keyed by ``ChequeNo``).

        For Shopify the ``ChequeNo`` (== ``LineRefNo2``) is the Shopify order
        number — the match key for building a deposit.
        """
        d = self.call(
            BANK_DEPOSIT_SERVICE, "getBankDepositLinkedUndepositedTransactions",
            currencyId=currency_id, size=size, number=1,
            searchExp="[]", sorder="desc", sname="TXN_DATE",
        )
        return _find_dict_list(d, "ChequeNo")

    def get_data_for_bank_deposit(self):
        d = self.call(BANK_DEPOSIT_SERVICE, "getDataForBankDeposit")
        return (d or {}).get("Data") if isinstance(d, dict) else d

    def create_bank_deposit(self, deposit_obj):
        """Create a bank deposit. ``deposit_obj`` = ``{BankDepositHeaderObj, BankDepositDetailArr}``.

        Posts ``bankDepositObjJson`` JSON-stringified (double-encoded). Returns the
        created header (``Data``, incl. ``Id``/``BankDepositNumber``).
        """
        env = self.call(
            BANK_DEPOSIT_SERVICE, "createBankDeposit",
            bankDepositObjJson=json.dumps(deposit_obj),
        )
        if not isinstance(env, dict) or not env.get("Result"):
            msg = env.get("Message") if isinstance(env, dict) else env
            raise WebMethodError("createBankDeposit failed: %s" % msg)
        data = env.get("Data", env)
        # the created deposit's Id/BankDepositNumber sit under BankDepositHeaderObj
        if isinstance(data, dict) and isinstance(data.get("BankDepositHeaderObj"), dict):
            return data["BankDepositHeaderObj"]
        return data

    def void_bank_deposit(self, bank_deposit_id):
        """Void/delete a bank deposit (returns its payments to undeposited funds)."""
        return self.call(BANK_DEPOSIT_SERVICE, "voidBankDeposit", bankDepositId=bank_deposit_id)
