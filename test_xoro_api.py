"""Tests for the Xoro API client (xoro_api.py).

Network is injected via a fake transport so the core logic (envelope
unwrapping, URL building, pagination, COA derivation) is tested without
hitting the live API. A separate live smoke test exercises real read-only
endpoints.
"""

import json
import os
import unittest

from xoro_api import XoroClient, XoroAPIError, build_chart_of_accounts


def envelope(data, *, result=True, error_code=0, message="ok", page=1, total_pages=1):
    return json.dumps(
        {
            "ConfirmFlag": False,
            "Data": data,
            "ErrorCode": error_code,
            "Message": message,
            "Result": result,
            "Page": page,
            "TotalPages": total_pages,
        }
    )


class RecordingTransport:
    """Fake transport. Records calls and returns queued (status, text) responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        status, text = self._responses.pop(0)
        return status, text


class CoreTest(unittest.TestCase):
    def make_client(self, responses, **kwargs):
        transport = RecordingTransport(responses)
        client = XoroClient(
            base_url="https://example.test",
            path_prefix="Xerp",
            transport=transport,
            **kwargs,
        )
        return client, transport

    def test_get_unwraps_data_on_success(self):
        client, _ = self.make_client([(200, envelope([{"id": 1}]))])
        result = client._get("invoice/getinvoice", {"page": 1})
        self.assertEqual(result, [{"id": 1}])

    def test_get_builds_url_with_path_prefix(self):
        client, transport = self.make_client([(200, envelope([]))])
        client._get("invoice/getinvoice", {})
        self.assertEqual(
            transport.calls[0]["url"],
            "https://example.test/Xerp/invoice/getinvoice",
        )

    def test_get_encodes_query_params(self):
        client, transport = self.make_client([(200, envelope([]))])
        client._get("invoice/getinvoice", {"page": 2, "ref_no": "CA CD034793"})
        url = transport.calls[0]["url"]
        self.assertIn("page=2", url)
        self.assertIn("ref_no=CA+CD034793", url)

    def test_error_result_raises_with_message_and_code(self):
        client, _ = self.make_client(
            [(200, envelope(None, result=False, error_code=42, message="bad filter"))]
        )
        with self.assertRaises(XoroAPIError) as ctx:
            client._get("invoice/getinvoice", {})
        self.assertEqual(ctx.exception.code, 42)
        self.assertIn("bad filter", str(ctx.exception))

    def test_http_error_status_raises(self):
        client, _ = self.make_client([(500, "Internal Server Error")])
        with self.assertRaises(XoroAPIError):
            client._get("invoice/getinvoice", {})

    def test_path_prefix_is_configurable(self):
        transport = RecordingTransport([(200, envelope([]))])
        client = XoroClient(
            base_url="https://example.test", path_prefix="xerp", transport=transport
        )
        client._get("invoice/getinvoice", {})
        self.assertEqual(
            transport.calls[0]["url"], "https://example.test/xerp/invoice/getinvoice"
        )

    def test_session_cookie_sent_when_configured(self):
        transport = RecordingTransport([(200, envelope([]))])
        client = XoroClient(
            base_url="https://example.test",
            path_prefix="Xerp",
            transport=transport,
            session_cookie="ABC123",
        )
        client._get("invoice/getinvoice", {})
        self.assertEqual(transport.calls[0]["headers"].get("Cookie"), ".ASPXAUTH=ABC123")


class PaginationTest(unittest.TestCase):
    def make_client(self, responses):
        transport = RecordingTransport(responses)
        client = XoroClient(
            base_url="https://example.test", path_prefix="Xerp", transport=transport
        )
        return client, transport

    def test_single_page_makes_one_call(self):
        client, transport = self.make_client(
            [(200, envelope([{"id": 1}], page=1, total_pages=1))]
        )
        rows = client._paginate("invoice/getinvoice", {}, page_param="page")
        self.assertEqual(rows, [{"id": 1}])
        self.assertEqual(len(transport.calls), 1)

    def test_concatenates_rows_across_pages(self):
        client, transport = self.make_client(
            [
                (200, envelope([{"id": 1}], page=1, total_pages=3)),
                (200, envelope([{"id": 2}], page=2, total_pages=3)),
                (200, envelope([{"id": 3}], page=3, total_pages=3)),
            ]
        )
        rows = client._paginate("invoice/getinvoice", {}, page_param="page")
        self.assertEqual(rows, [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(len(transport.calls), 3)

    def test_increments_named_page_param(self):
        client, transport = self.make_client(
            [
                (200, envelope([{"id": 1}], page=1, total_pages=2)),
                (200, envelope([{"id": 2}], page=2, total_pages=2)),
            ]
        )
        client._paginate("accounting/getgltransactions", {}, page_param="page_number")
        self.assertIn("page_number=1", transport.calls[0]["url"])
        self.assertIn("page_number=2", transport.calls[1]["url"])

    def test_extractor_pulls_nested_list(self):
        gl_page = lambda rows, p, tp: envelope(
            {"transactionList": rows, "deletedTxnNumbers": []}, page=p, total_pages=tp
        )
        client, _ = self.make_client(
            [
                (200, gl_page([{"TxnId": "a"}], 1, 2)),
                (200, gl_page([{"TxnId": "b"}], 2, 2)),
            ]
        )
        rows = client._paginate(
            "accounting/getgltransactions",
            {},
            page_param="page_number",
            extract=lambda data: data["transactionList"],
        )
        self.assertEqual(rows, [{"TxnId": "a"}, {"TxnId": "b"}])


class EndpointMethodTest(unittest.TestCase):
    def make_client(self, responses):
        transport = RecordingTransport(responses)
        client = XoroClient(
            base_url="https://example.test", path_prefix="Xerp", transport=transport
        )
        return client, transport

    def test_get_invoices_paginates_with_filters(self):
        client, transport = self.make_client(
            [(200, envelope([{"invoiceHeader": {"AmountDue": 574.0}}]))]
        )
        rows = client.get_invoices(ref_no="10174")
        self.assertEqual(len(rows), 1)
        url = transport.calls[0]["url"]
        self.assertIn("/Xerp/invoice/getinvoice", url)
        self.assertIn("ref_no=10174", url)
        self.assertIn("page=1", url)

    def test_get_gl_transactions_uses_page_number_and_extracts_list(self):
        gl_data = {"transactionList": [{"GLCode": "2226"}], "deletedTxnNumbers": []}
        client, transport = self.make_client([(200, envelope(gl_data))])
        rows = client.get_gl_transactions(
            start_date="06/01/2026", end_date="06/23/2026"
        )
        self.assertEqual(rows, [{"GLCode": "2226"}])
        url = transport.calls[0]["url"]
        self.assertIn("/Xerp/accounting/getgltransactions", url)
        self.assertIn("start_date=06%2F01%2F2026", url)
        self.assertIn("page_number=1", url)
        self.assertIn("page_size=", url)

    def test_create_bank_statement_posts_wrapped_body(self):
        client, transport = self.make_client([(200, envelope({"ok": True}))])
        header = {"ImportTypeId": 10, "StartDate": "06/01/2026"}
        lines = [{"AccntId": "ABC", "Amount": 15230, "TypeName": "debit"}]
        client.create_bank_statement(header, lines)
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertIn("/Xerp/bankdeposit/createstatement", call["url"])
        body = json.loads(call["body"])
        self.assertEqual(body["BankStatementHeader"], header)
        self.assertEqual(body["BankStatementLineArr"], lines)

    def test_import_credit_memo_posts_payload(self):
        client, transport = self.make_client([(200, envelope({"ok": True}))])
        payload = {"creditMemoHeader": {"RefNo": "10174"}, "creditMemoItemLineArry": []}
        client.import_credit_memo(payload)
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertIn("/Xerp/creditmemo/import", call["url"])
        self.assertEqual(json.loads(call["body"]), payload)


class FromConfigTest(unittest.TestCase):
    def test_reads_base_url_and_prefix_from_file_and_cookie_from_env(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "xoro_config.json")
            with open(cfg, "w") as f:
                json.dump({"base_url": "https://m.test", "path_prefix": "Xerp"}, f)
            os.environ["XORO_SESSION_COOKIE"] = "COOKIEVAL"
            try:
                client = XoroClient.from_config(config_path=cfg)
            finally:
                del os.environ["XORO_SESSION_COOKIE"]
        self.assertEqual(client.base_url, "https://m.test")
        self.assertEqual(client.path_prefix, "Xerp")
        self.assertEqual(client.session_cookie, "COOKIEVAL")

    def test_missing_config_uses_defaults(self):
        client = XoroClient.from_config(config_path="/nonexistent/xoro_config.json")
        self.assertEqual(client.base_url, "https://momentum.xoro.one")
        self.assertEqual(client.path_prefix, "Xerp")


class ChartOfAccountsTest(unittest.TestCase):
    def test_build_maps_glcode_to_id_name_type(self):
        rows = [
            {
                "GLCode": "2226",
                "F_AccountingId": "268196F9",
                "F_AccountingName": "GST/HST Payable",
                "F_AccountingTypeName": "OtherCurrentLiability",
            }
        ]
        coa = build_chart_of_accounts(rows)
        self.assertEqual(
            coa["2226"],
            {
                "id": "268196F9",
                "name": "GST/HST Payable",
                "type": "OtherCurrentLiability",
            },
        )

    def test_build_dedups_repeated_glcodes(self):
        rows = [
            {"GLCode": "2105", "F_AccountingId": "X", "F_AccountingName": "Amex 4009",
             "F_AccountingTypeName": "CreditCard"},
            {"GLCode": "2105", "F_AccountingId": "X", "F_AccountingName": "Amex 4009",
             "F_AccountingTypeName": "CreditCard"},
        ]
        coa = build_chart_of_accounts(rows)
        self.assertEqual(len(coa), 1)

    def test_build_skips_rows_without_glcode(self):
        rows = [
            {"GLCode": "", "F_AccountingId": "X", "F_AccountingName": "n",
             "F_AccountingTypeName": "t"},
            {"GLCode": None, "F_AccountingId": "Y", "F_AccountingName": "m",
             "F_AccountingTypeName": "t"},
        ]
        self.assertEqual(build_chart_of_accounts(rows), {})


if __name__ == "__main__":
    unittest.main()
