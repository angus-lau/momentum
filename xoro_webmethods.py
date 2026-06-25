"""Self-healing client for Xoro's internal ASP.NET ScriptService API.

The Xoro UI runs on ``/WebServices/{Service}.asmx/{method}`` endpoints (POST
JSON of named params, response ``{"d": ...}``), authenticated by the session
cookie. This client wraps that surface and, when a call bounces to the login
page (expired cookie), transparently re-logs-in via ``xoro_login.login()`` and
retries once — so callers never deal with auth.

Used by the reconciliation flow: ``getBankReconcileAccountList``,
``getBankReconciledTransactions``, ``saveJournalEntry``, ``finishBankRec``, etc.
"""

import json
import os
import urllib.request

import xoro_login

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "xoro_config.json")
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")

DEFAULT_BASE_URL = "https://momentum.xoro.one"
LOGIN_MARKER = "login.aspx"


class WebMethodError(Exception):
    """Raised when an .asmx call fails (HTTP error, or auth that won't refresh)."""


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
