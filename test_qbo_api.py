import os
import tempfile
import unittest
from unittest import mock

import qbo_api


class EnvTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp()
        os.write(fd, b"A=1\nQBO_REFRESH_TOKEN=old")
        os.close(fd)
        self.patch = mock.patch.object(qbo_api, "ENV_PATH", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        os.unlink(self.path)

    def test_rotated_refresh_token_replaces_old(self):
        self.assertEqual(qbo_api.save_tokens({"access_token": "acc", "refresh_token": "new"}), "acc")
        self.assertEqual(qbo_api.env("QBO_REFRESH_TOKEN"), "new")
        self.assertEqual(open(self.path).read().count("QBO_REFRESH_TOKEN"), 1)

    def test_new_key_appended_without_touching_others(self):
        qbo_api.set_env("QBO_REALM_ID", "123")
        self.assertEqual((qbo_api.env("A"), qbo_api.env("QBO_REALM_ID")), ("1", "123"))


if __name__ == "__main__":
    unittest.main()
