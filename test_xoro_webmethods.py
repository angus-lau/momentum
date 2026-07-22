"""Tests for xoro_webmethods.py — the self-healing .asmx caller.

Network is injected via a fake transport. The class wraps Xoro's ASP.NET
ScriptService endpoints (``/WebServices/{svc}.asmx/{method}``), unwraps the
``{"d": ...}`` envelope, and re-logs-in + retries once when a call bounces to
the login page (expired cookie).
"""

import datetime
import json
import unittest

from xoro_webmethods import (
    WebMethodClient,
    WebMethodError,
    build_bank_statement,
    activity_rows_to_lines,
    _fmt_stmt_amount,
    _fmt_stmt_date,
)


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


class BankStatementFormatTest(unittest.TestCase):
    def test_amount_trims_trailing_zeros_like_xoro(self):
        self.assertEqual(_fmt_stmt_amount(-1.00), "-1")
        self.assertEqual(_fmt_stmt_amount(-401.90), "-401.9")
        self.assertEqual(_fmt_stmt_amount(-401.95), "-401.95")
        self.assertEqual(_fmt_stmt_amount(10464.27), "10464.27")

    def test_amount_string_passthrough(self):
        self.assertEqual(_fmt_stmt_amount("-1.005"), "-1.005")

    def test_date_iso_to_slash_no_leading_zeros(self):
        self.assertEqual(_fmt_stmt_date("2026-06-06"), "6/6/2026")

    def test_date_object_and_slash_passthrough(self):
        self.assertEqual(_fmt_stmt_date(datetime.date(2026, 1, 21)), "1/21/2026")
        self.assertEqual(_fmt_stmt_date("6/6/2026"), "6/6/2026")

    def test_date_us_slash_strips_leading_zeros(self):
        self.assertEqual(_fmt_stmt_date("05/31/2026"), "5/31/2026")


class ActivityRowsTest(unittest.TestCase):
    ROWS = [
        {"Date": "05/31/2026", "Description": "ZONOS (CROSS-BORDER)", "Amount": "30.14"},
        {"Date": "05/25/2026", "Description": "AUTOPAY PAYMENT - THANK YOU", "Amount": "-433.21"},
    ]

    def test_credit_card_flips_sign(self):
        lines = activity_rows_to_lines(self.ROWS, credit_card=True)
        stmt = build_bank_statement("FACC1", lines)["BankStatementLineArr"]
        self.assertEqual(stmt[0]["Amount"], "-30.14")   # charge -> negative
        self.assertEqual(stmt[0]["Date"], "5/31/2026")
        self.assertEqual(stmt[0]["Payee"], "ZONOS (CROSS-BORDER)")
        self.assertEqual(stmt[0]["Description"], "ZONOS (CROSS-BORDER)")
        self.assertEqual(stmt[1]["Amount"], "433.21")   # payment -> positive

    def test_no_flip_when_not_credit_card(self):
        lines = activity_rows_to_lines(self.ROWS, credit_card=False)
        stmt = build_bank_statement("FACC1", lines)["BankStatementLineArr"]
        self.assertEqual(stmt[0]["Amount"], "30.14")
        self.assertEqual(stmt[1]["Amount"], "-433.21")


class BuildBankStatementTest(unittest.TestCase):
    def test_builds_header_and_lines(self):
        payload = build_bank_statement(
            "FACC1",
            [
                {"date": "2026-06-06", "amount": -1.00, "payee": "P", "description": "D",
                 "reference": "R", "cheque": "C1"},
                {"date": "2026-06-07", "amount": 100.0},
            ],
            end_balance="45000",
        )
        self.assertEqual(
            payload["BankStatementHeader"],
            {"ImportTypeId": 10, "StartDate": None, "EndDate": None,
             "StartBalance": None, "EndBalance": "45000"},
        )
        lines = payload["BankStatementLineArr"]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["AccntId"], "FACC1")
        self.assertEqual(lines[0]["Amount"], "-1")
        self.assertEqual(lines[0]["Date"], "6/6/2026")
        self.assertEqual(lines[0]["Payee"], "P")
        self.assertEqual(lines[0]["ChequeNumber"], "C1")
        self.assertEqual(lines[0]["Seq"], 1)
        self.assertEqual(lines[1]["Seq"], 2)
        self.assertEqual(lines[1]["Amount"], "100")
        # defaults for the sparse line
        self.assertEqual(lines[1]["Payee"], "")
        self.assertIsNone(lines[1]["ChequeNumber"])
        self.assertFalse(lines[1]["HasError"])


class CreateBankStatementTest(unittest.TestCase):
    def test_posts_stringified_bankstmtdata_and_returns_envelope(self):
        # uploadBankStatementManual double-encodes: d is a stringified envelope.
        ok = json.dumps({"d": json.dumps({"Result": True, "Message": "", "ErrorCode": 0})})
        c, t = client([(200, ok)])
        env = c.create_bank_statement(
            "FACC1", [{"date": "2026-06-06", "amount": -1.0}], end_balance="10",
        )
        self.assertTrue(env["Result"])
        call = t.calls[0]
        self.assertEqual(
            call["url"],
            "https://example.test/WebServices/ConnectBankWebMethods.asmx/uploadBankStatementManual",
        )
        body = json.loads(call["body"])
        # the ScriptService param is a JSON *string*, not a nested object
        self.assertIsInstance(body["bankStmtData"], str)
        inner = json.loads(body["bankStmtData"])
        self.assertEqual(inner["BankStatementLineArr"][0]["AccntId"], "FACC1")
        self.assertEqual(inner["BankStatementLineArr"][0]["Amount"], "-1")

    def test_result_false_raises_with_message(self):
        bad = json.dumps({"d": json.dumps({"Result": False, "Message": "nope"})})
        c, _ = client([(200, bad)])
        with self.assertRaises(WebMethodError) as ctx:
            c.create_bank_statement("FACC1", [{"date": "2026-06-06", "amount": -1.0}])
        self.assertIn("nope", str(ctx.exception))

    def test_get_bank_statement_accounts_extracts_list(self):
        resp = json.dumps({"d": json.dumps(
            {"Result": True, "Data": {"BankAccountList": [{"FAccountId": "F1"}]}})})
        c, _ = client([(200, resp)])
        self.assertEqual(c.get_bank_statement_accounts(), [{"FAccountId": "F1"}])


class StartReconciliationTest(unittest.TestCase):
    @staticmethod
    def _d(obj):
        # ScriptService double-encodes: {"d": "<json string>"}
        return json.dumps({"d": json.dumps(obj)})

    def test_payload_is_stringified_brheaderobj_with_all_fields(self):
        ok = self._d({"Result": True, "Data": {"Id": 941, "AccountEndBal": -489.71}})
        c, t = client([(200, ok)])
        data = c.start_reconciliation(
            "FACC1", "-489.71", "06/02/2026",
            currency_id=1001, beginning_balance=-489.71,   # supplied -> single call
        )
        self.assertEqual(data["Id"], 941)
        call = t.calls[0]
        self.assertEqual(
            call["url"],
            "https://example.test/WebServices/BankReconcileWebMethods.asmx/addBankRecHeader",
        )
        body = json.loads(call["body"])
        self.assertIsInstance(body["brHeaderObj"], str)          # stringified, like bankStmtData
        header = json.loads(body["brHeaderObj"])
        self.assertEqual(header, {
            "AccountId": "FACC1", "CurrencyId": 1001,
            "AccountEndBal": -489.71, "LastStatementDate": "06/02/2026",
            "AccountBegBal": -489.71,
        })

    def test_beginning_balance_and_currency_auto_resolved(self):
        last = self._d({"Result": True, "Data": {"beginningBal": -433.21}})
        accts = self._d({"Result": True, "Data": {"BankAccountList": [
            {"FAccountingId": "FACC1", "CurrencyId": 1001}]}})
        add = self._d({"Result": True, "Data": {"Id": 942}})
        c, t = client([(200, last), (200, accts), (200, add)])
        c.start_reconciliation("FACC1", "-489.71", "06/02/2026")
        header = json.loads(json.loads(t.calls[2]["body"])["brHeaderObj"])
        self.assertEqual(header["AccountBegBal"], -433.21)   # auto-carried
        self.assertEqual(header["CurrencyId"], 1001)          # resolved from account

    def test_result_false_raises(self):
        bad = self._d({"Result": False, "Message": "no good"})
        c, _ = client([(200, bad)])
        with self.assertRaises(WebMethodError) as ctx:
            c.start_reconciliation("FACC1", "-489.71", "06/02/2026",
                                   currency_id=1001, beginning_balance=0)
        self.assertIn("no good", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
