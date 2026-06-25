"""Tests for xoro_webmethods.py — the self-healing .asmx caller.

Network is injected via a fake transport. The class wraps Xoro's ASP.NET
ScriptService endpoints (``/WebServices/{svc}.asmx/{method}``), unwraps the
``{"d": ...}`` envelope, and re-logs-in + retries once when a call bounces to
the login page (expired cookie).
"""

import json
import unittest

from xoro_webmethods import WebMethodClient, WebMethodError


class RecordingTransport:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return self._responses.pop(0)


def client(responses, cookie="COOKIE0", login_fn=None):
    transport = RecordingTransport(responses)
    c = WebMethodClient(
        base_url="https://example.test",
        cookie=cookie,
        login_fn=login_fn or (lambda: "REFRESHED"),
        transport=transport,
    )
    return c, transport


class CallTest(unittest.TestCase):
    def test_posts_json_to_asmx_url_with_cookie(self):
        c, t = client([(200, '{"d": {"ok": true}}')])
        c.call("JournalEntryWebMethods", "getJournalEntryObjSchema")
        call = t.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(
            call["url"],
            "https://example.test/WebServices/JournalEntryWebMethods.asmx/getJournalEntryObjSchema",
        )
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertEqual(call["headers"]["Cookie"], "COOKIE0")

    def test_sends_named_params_as_json_body(self):
        c, t = client([(200, '{"d": []}')])
        c.call("BankReconcileWebMethods", "getBankReconciledTransactions",
               size=50, number=1, bankRecId="ABC")
        self.assertEqual(
            json.loads(t.calls[0]["body"]),
            {"size": 50, "number": 1, "bankRecId": "ABC"},
        )

    def test_no_params_sends_empty_object(self):
        c, t = client([(200, '{"d": null}')])
        c.call("AccountingWebMethods", "getBankReconcileAccountList")
        self.assertEqual(t.calls[0]["body"], "{}")

    def test_unwraps_object_d(self):
        c, _ = client([(200, '{"d": {"Id": 5, "Name": "x"}}')])
        self.assertEqual(c.call("S", "m"), {"Id": 5, "Name": "x"})

    def test_unwraps_json_string_d(self):
        # ScriptService sometimes double-encodes: d is itself a JSON string.
        c, _ = client([(200, '{"d": "[{\\"GLCode\\": \\"2105\\"}]"}')])
        self.assertEqual(c.call("S", "m"), [{"GLCode": "2105"}])

    def test_http_500_raises(self):
        c, _ = client([(500, "Server Error")])
        with self.assertRaises(WebMethodError):
            c.call("S", "m")


class SelfHealTest(unittest.TestCase):
    def test_login_bounce_triggers_relogin_and_retry(self):
        calls = {"n": 0}
        def fake_login():
            calls["n"] += 1
            return "FRESHCOOKIE"
        c, t = client(
            [
                (302, "https://example.test/Authentication/login.aspx"),
                (200, '{"d": {"ok": true}}'),
            ],
            login_fn=fake_login,
        )
        result = c.call("S", "m")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls["n"], 1)                       # re-logged in once
        self.assertEqual(t.calls[1]["headers"]["Cookie"], "FRESHCOOKIE")  # retried with new cookie

    def test_repeated_bounce_raises_no_infinite_loop(self):
        c, t = client(
            [
                (302, "https://example.test/Authentication/login.aspx"),
                (302, "https://example.test/Authentication/login.aspx"),
            ],
            login_fn=lambda: "STILLBAD",
        )
        with self.assertRaises(WebMethodError):
            c.call("S", "m")
        self.assertEqual(len(t.calls), 2)  # original + one retry, then give up


if __name__ == "__main__":
    unittest.main()
