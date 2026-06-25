"""Programmatic Xoro login → fresh session cookie.

Xoro's login is a classic ASP.NET WebForms postback (no 2FA enforced on this
account). We GET the login page to pick up the hidden ``__VIEWSTATE`` family,
POST the credentials, and harvest the ``__Auth`` + ``ASP.NET_SessionId``
cookies that authenticate every subsequent ``/WebServices`` and ``/xerp`` call.

``login()`` writes the assembled Cookie header to ``.env`` (gitignored) as
``XORO_COOKIE`` and returns it. ``xoro_api`` calls this automatically when a
request bounces to the login page (expired cookie).
"""

import http.cookiejar
import os
import re
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")

DEFAULT_BASE_URL = "https://momentum.xoro.one"
LOGIN_PATH = "/Authentication/login.aspx"

# WebForms hidden fields we must echo back on the postback.
HIDDEN_FIELDS = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")


def extract_hidden_fields(html):
    """Pull the ASP.NET WebForms hidden fields out of the login page HTML."""
    fields = {}
    for name in HIDDEN_FIELDS:
        m = re.search(
            r'name="%s"[^>]*\bvalue="([^"]*)"' % re.escape(name), html
        ) or re.search(
            r'id="%s"[^>]*\bvalue="([^"]*)"' % re.escape(name), html
        )
        if not m:
            raise ValueError("login page missing hidden field: %s" % name)
        fields[name] = m.group(1)
    return fields


def build_cookie_header(set_cookie_values):
    """Assemble a ``name=value; ...`` Cookie header from Set-Cookie strings.

    Later values win; cookies being deleted (empty value) are skipped.
    """
    jar = {}
    order = []
    for raw in set_cookie_values:
        pair = raw.split(";", 1)[0].strip()
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name = name.strip()
        if not value.strip():
            jar.pop(name, None)
            continue
        if name not in jar:
            order.append(name)
        jar[name] = value.strip()
    return "; ".join("%s=%s" % (n, jar[n]) for n in order if n in jar)


def _read_env_credentials():
    creds = {}
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH):
            line = line.strip()
            if line.startswith("XORO_USERNAME=") or line.startswith("XORO_PASSWORD="):
                k, _, v = line.partition("=")
                creds[k] = v
    return creds.get("XORO_USERNAME"), creds.get("XORO_PASSWORD")


def _write_cookie_to_env(cookie_header):
    lines = []
    if os.path.exists(ENV_PATH):
        lines = [
            l for l in open(ENV_PATH).read().splitlines()
            if not l.startswith("XORO_COOKIE=")
        ]
    lines.append("XORO_COOKIE=" + cookie_header)
    open(ENV_PATH, "w").write("\n".join(l for l in lines if l.strip()) + "\n")


def login(username=None, password=None, base_url=DEFAULT_BASE_URL, persist=True):
    """Log in to Xoro and return a Cookie header authenticating API calls."""
    if username is None or password is None:
        username, password = _read_env_credentials()
    if not username or not password:
        raise ValueError("Xoro credentials missing (set XORO_USERNAME/XORO_PASSWORD in .env)")

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login_url = base_url + LOGIN_PATH

    # 1. GET the login page for the session cookie + hidden fields.
    with opener.open(login_url, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    fields = extract_hidden_fields(html)

    # 2. POST credentials (OTP fields left empty — no 2FA on this account).
    form = {
        **fields,
        "loginCtrl$UserName": username,
        "loginCtrl$Password": password,
        "loginCtrl$RememberMe": "on",
        "loginCtrl$LoginButton": "Login",
    }
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        login_url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with opener.open(req, timeout=30) as r:
        landed = r.geturl()

    # 3. Harvest the authenticated cookies from the jar.
    names = [c.name for c in jar]
    if "__Auth" not in names:
        raise ValueError(
            "login did not return an auth cookie (landed on %s) — check credentials" % landed
        )
    header = "; ".join("%s=%s" % (c.name, c.value) for c in jar)

    if persist:
        _write_cookie_to_env(header)
    return header


if __name__ == "__main__":
    h = login()
    print("logged in; cookie header length:", len(h))
