"""Tests for xoro_login.py — the pure parsing helpers.

The live login itself hits the network and is verified by a separate smoke
test; here we cover the deterministic parts: pulling the WebForms hidden
fields out of the login page and assembling a Cookie header from Set-Cookie.
"""

import unittest

from xoro_login import extract_hidden_fields, build_cookie_header


LOGIN_HTML = """
<html><body>
<form method="post" action="./login.aspx" id="loginForm">
  <input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="/wEPDwUKabc+123/x=" />
  <input type="hidden" name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="C2EE9ABB" />
  <input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="/wEdAAxyz==" />
  <input name="loginCtrl$UserName" id="UserName" type="text" />
  <input name="loginCtrl$Password" id="Password" type="password" />
</form>
</body></html>
"""


class ExtractHiddenFieldsTest(unittest.TestCase):
    def test_pulls_viewstate_generator_and_eventvalidation(self):
        fields = extract_hidden_fields(LOGIN_HTML)
        self.assertEqual(fields["__VIEWSTATE"], "/wEPDwUKabc+123/x=")
        self.assertEqual(fields["__VIEWSTATEGENERATOR"], "C2EE9ABB")
        self.assertEqual(fields["__EVENTVALIDATION"], "/wEdAAxyz==")

    def test_ignores_non_hidden_inputs(self):
        fields = extract_hidden_fields(LOGIN_HTML)
        self.assertNotIn("loginCtrl$UserName", fields)

    def test_missing_field_raises(self):
        with self.assertRaises(ValueError):
            extract_hidden_fields("<html><form>no hidden fields</form></html>")


class BuildCookieHeaderTest(unittest.TestCase):
    def test_joins_name_value_pairs(self):
        set_cookies = [
            "ASP.NET_SessionId=abc123; path=/; HttpOnly",
            "__Auth=BIGTOKEN; path=/; HttpOnly; secure",
        ]
        header = build_cookie_header(set_cookies)
        self.assertEqual(header, "ASP.NET_SessionId=abc123; __Auth=BIGTOKEN")

    def test_later_cookie_value_overrides_earlier(self):
        set_cookies = [
            "__Auth=OLD; path=/",
            "__Auth=NEW; path=/; HttpOnly",
        ]
        header = build_cookie_header(set_cookies)
        self.assertEqual(header, "__Auth=NEW")

    def test_skips_deleted_cookies(self):
        set_cookies = [
            "__Auth=TOKEN; path=/",
            "stale=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/",
        ]
        header = build_cookie_header(set_cookies)
        self.assertEqual(header, "__Auth=TOKEN")


if __name__ == "__main__":
    unittest.main()
