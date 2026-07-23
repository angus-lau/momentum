"""One-time OAuth handshake to obtain a Shopify offline Admin API access token.

Dev Dashboard custom apps don't expose an Admin API access token directly, so we
run the OAuth Authorization Code Grant once:

  1. open the store's authorize/install page (you click Approve),
  2. Shopify redirects back with an authorization code,
  3. we exchange the code (client_id + client_secret) for an offline access token,
  4. the token is written to .env as SHOPIFY_ADMIN_TOKEN and never expires.

Reads SHOPIFY_STORE / SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET from .env.

PREREQUISITE: in the app's Dev Dashboard -> Configuration, add this exact
allowed redirect URL:  http://localhost:3456/callback

Run:  python3 shopify_oauth.py
"""

import http.server
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
PORT = 3456
REDIRECT_URI = "http://localhost:%d/callback" % PORT
SCOPES = "read_orders,read_shopify_payments_payouts"


def env(key):
    if not os.path.exists(ENV_PATH):
        return None
    for line in open(ENV_PATH):
        line = line.strip()
        if line.startswith(key + "="):
            return line.partition("=")[2].strip().strip('"').strip("'")
    return None


def set_env(key, value):
    lines, found = [], False
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH):
            if line.strip().startswith(key + "="):
                lines.append("%s=%s\n" % (key, value))
                found = True
            else:
                lines.append(line)
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append("%s=%s\n" % (key, value))
    with open(ENV_PATH, "w") as f:
        f.writelines(lines)


STORE = env("SHOPIFY_STORE")
CLIENT_ID = env("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = env("SHOPIFY_CLIENT_SECRET")
if not all([STORE, CLIENT_ID, CLIENT_SECRET]):
    sys.exit("Missing SHOPIFY_STORE / SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET in .env")

STATE = secrets.token_urlsafe(16)
AUTHORIZE_URL = (
    "https://%s/admin/oauth/authorize?client_id=%s&scope=%s&redirect_uri=%s&state=%s"
    % (STORE, CLIENT_ID, SCOPES, urllib.parse.quote(REDIRECT_URI, safe=""), STATE)
)

# Persist the URL (with its one-time state) so it can be opened manually if the
# browser doesn't auto-launch.
URL_FILE = os.path.join(SCRIPT_DIR, "_oauth_url.txt")
with open(URL_FILE, "w") as _f:
    _f.write(AUTHORIZE_URL + "\n")

result = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        if state != STATE or not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"State mismatch or missing code.")
            result["error"] = "state mismatch or missing code"
            return
        body = json.dumps({
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
        }).encode()
        req = urllib.request.Request(
            "https://%s/admin/oauth/access_token" % STORE,
            data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                token = json.loads(r.read().decode()).get("access_token")
        except Exception as e:  # noqa: BLE001
            self.send_response(500)
            self.end_headers()
            self.wfile.write(("Token exchange failed: %s" % e).encode())
            result["error"] = str(e)
            return
        result["token"] = token
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Done - access token captured. You can close this tab.</h2>")

    def log_message(self, *args):
        pass


def main():
    print("Make sure %s is an allowed redirect URL in the app's Configuration.\n" % REDIRECT_URI)
    print("Authorize URL (opening in your browser):\n%s\n" % AUTHORIZE_URL)
    try:
        webbrowser.open(AUTHORIZE_URL)
    except Exception:
        print("(couldn't auto-open; paste the URL above into a browser signed into the store)")
    srv = http.server.HTTPServer(("localhost", PORT), Handler)
    print("Waiting for the OAuth callback on %s ..." % REDIRECT_URI)
    while "token" not in result and "error" not in result:
        srv.handle_request()
    if result.get("token"):
        set_env("SHOPIFY_ADMIN_TOKEN", result["token"])
        print("\n✅ Offline access token saved to .env as SHOPIFY_ADMIN_TOKEN")
    else:
        print("\n❌ Failed: %s" % result.get("error"))


if __name__ == "__main__":
    main()
