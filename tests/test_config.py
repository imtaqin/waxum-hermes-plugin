import os
import unittest

from waxum_hermes_plugin.config import WaxumConfig, check_requirements, validate_config
from waxum_hermes_plugin.exceptions import WaxumConfigError


class ConfigTests(unittest.TestCase):
    def setUp(self):
        for k in ("WAXUM_BASE_URL", "WAXUM_TOKEN", "WAXUM_SESSION_ID"):
            os.environ.pop(k, None)

    def test_from_env(self):
        os.environ["WAXUM_TOKEN"] = "tok"
        os.environ["WAXUM_SESSION_ID"] = "s1"
        cfg = WaxumConfig.from_platform_config({})
        self.assertEqual(cfg.token, "tok")
        self.assertEqual(cfg.session_id, "s1")
        self.assertEqual(cfg.base_url, "http://127.0.0.1:3451")

    def test_dict_config_overrides_env(self):
        os.environ["WAXUM_TOKEN"] = "env-tok"
        os.environ["WAXUM_SESSION_ID"] = "env-s"
        cfg = WaxumConfig.from_platform_config({"token": "cfg-tok", "session_id": "cfg-s", "base_url": "http://x/"})
        self.assertEqual(cfg.token, "cfg-tok")
        self.assertEqual(cfg.session_id, "cfg-s")
        self.assertEqual(cfg.base_url, "http://x")  # trailing slash stripped

    def test_missing_required_raises(self):
        with self.assertRaises(WaxumConfigError):
            WaxumConfig.from_platform_config({})

    def test_check_and_validate(self):
        self.assertFalse(check_requirements({}))
        self.assertIsNotNone(validate_config({}))
        self.assertTrue(check_requirements({"token": "t", "session_id": "s"}))
        self.assertIsNone(validate_config({"token": "t", "session_id": "s"}))


if __name__ == "__main__":
    unittest.main()
