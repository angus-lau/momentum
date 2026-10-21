"""QuickBooks Online (SMWCO) API: one-time sign-in plus a small read client.

ONE-TIME SETUP (developer.intuit.com, signed in as an SMWCO QuickBooks admin):
  * Dashboard -> Create an app -> QuickBooks Online and Payments -> scope: com.intuit.quickbooks.accounting only
  * Keys & credentials (Production): add redirect URI  http://localhost:3457/callback
  * put the keys in .env as QBO_CLIENT_ID / QBO_CLIENT_SECRET
  * run:  python3 qbo_api.py auth   -> approve in the browser; QBO_REFRESH_TOKEN and QBO_REALM_ID are saved to .env
    (if Intuit refuses the localhost URI for production keys, use Intuit's OAuth 2.0 Playground instead and
    paste its refresh token / realm id into .env by hand)

USE:
    from qbo_api import QBO
    q = QBO()
    q.query("select * from Account where AccountType = 'Bank'")
    q.report("GeneralLedger", start_date="2026-09-01", end_date="2026-09-30")
    q.post("journalentry", {...})                                  # writes: create/update
    q.post("journalentry", {"Id": "1", "SyncToken": "0"}, operation="delete")
    python3 qbo_api.py test      # prints the company name
"""
import base64
import http.server
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
PORT = 3457
REDIRECT_URI = "http://localhost:%d/callback" % PORT
AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
API_BASE = "https://quickbooks.api.intuit.com/v3/company/%s/"
MINOR_VERSION = "75"


def env(key):
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH):
            if line.strip().startswith(key + "="):
                return line.partition("=")[2].strip().strip('"').strip("'")
    return None


def set_env(key, value):
    lines = open(ENV_PATH).readlines() if os.path.exists(ENV_PATH) else []
    out = [("%s=%s\n" % (key, value)) if l.strip().startswith(key + "=") else l for l in lines]
    if out == lines and not any(l.strip().startswith(key + "=") for l in lines):
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append("%s=%s\n" % (key, value))
    with open(ENV_PATH, "w") as f:
        f.writelines(out)


def _token_request(form):
    cid, secret = env("QBO_CLIENT_ID"), env("QBO_CLIENT_SECRET")
    if not cid or not secret:
        raise SystemExit("Missing QBO_CLIENT_ID / QBO_CLIENT_SECRET in .env - see this file's docstring")
    basic = base64.b64encode(("%s:%s" % (cid, secret)).encode()).decode()
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Authorization": "Basic " + basic, "Accept": "application/json",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def save_tokens(payload):
    """Intuit rotates the refresh token: always keep the newest one or the next run fails."""
    if payload.get("refresh_token"):
        set_env("QBO_REFRESH_TOKEN", payload["refresh_token"])
    return payload["access_token"]


class QBO:
    def __init__(self):
        self.realm = env("QBO_REALM_ID")
        if not self.realm or not env("QBO_REFRESH_TOKEN"):
            raise SystemExit("Not signed in yet: run  python3 qbo_api.py auth")
        self.token = save_tokens(_token_request({"grant_type": "refresh_token",
                                                 "refresh_token": env("QBO_REFRESH_TOKEN")}))

    def get(self, path, **params):
        return self._send(path, None, params)

    def post(self, entity, body, **params):
        """Create/update an entity, e.g. post("journalentry", {...}); delete with
        post("journalentry", {"Id": .., "SyncToken": ..}, operation="delete")."""
        return self._send(entity, body, params)

    def _send(self, path, body, params):
        params["minorversion"] = MINOR_VERSION
        url = (API_BASE % self.realm) + path + "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, headers=headers, method="GET" if body is None else "POST",
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:  # Intuit puts the reason in the body
            raise RuntimeError("QBO %s %s: %s" % (e.code, path, e.read().decode()[:500])) from None

    def query(self, sql):
        """Intuit's SQL-like query; returns the entity list (pages of up to 1000)."""
        rows, start = [], 1
        while True:
            res = self.get("query", query="%s STARTPOSITION %d MAXRESULTS 1000" % (sql, start))["QueryResponse"]
            page = next((v for v in res.values() if isinstance(v, list)), [])
            rows += page
            if len(page) < 1000:
                return rows
            start += 1000

    def report(self, name, **params):
        return self.get("reports/" + name, **params)


def auth():
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": env("QBO_CLIENT_ID"), "redirect_uri": REDIRECT_URI, "response_type": "code",
        "scope": "com.intuit.quickbooks.accounting", "state": state})
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            p = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if p.get("state", [None])[0] != state or "code" not in p:
                result["error"] = "state mismatch or missing code"
            else:
                try:
                    save_tokens(_token_request({"grant_type": "authorization_code", "code": p["code"][0],
                                                "redirect_uri": REDIRECT_URI}))
                    set_env("QBO_REALM_ID", p["realmId"][0])
                    result["ok"] = p["realmId"][0]
                except Exception as e:  # noqa: BLE001
                    result["error"] = str(e)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(("Done - you can close this tab." if "ok" in result else "Failed: %s" % result.get("error")).encode())

        def log_message(self, *a):
            pass

    print("Opening Intuit sign-in. If the browser doesn't open, visit:\n" + url)
    webbrowser.open(url)
    server = http.server.HTTPServer(("localhost", PORT), Handler)
    while not result:
        server.handle_request()
    if "error" in result:
        raise SystemExit("Sign-in failed: " + result["error"])
    print("Signed in. Realm %s; QBO_REFRESH_TOKEN and QBO_REALM_ID saved to .env" % result["ok"])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "auth":
        auth()
    elif cmd == "test":
        print(QBO().get("companyinfo/%s" % env("QBO_REALM_ID"))["CompanyInfo"]["CompanyName"])
    else:
        print(__doc__)
