import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from waxum_hermes_plugin.client import WaxumClient
from waxum_hermes_plugin.config import WaxumConfig
from waxum_hermes_plugin.exceptions import WaxumAuthError, WaxumRequestError, WaxumSessionUnavailable


def _cfg(**overrides):
    return WaxumConfig(base_url="http://waxum.local", token="tok", session_id="s1", max_retries=2,
                        backoff_base=0.001, backoff_max=0.002, **overrides)


class FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ClientTests(unittest.TestCase):
    def test_get_success(self):
        client = WaxumClient(_cfg())
        with patch("waxum_hermes_plugin.client.urlrequest.urlopen", return_value=FakeResponse({"status": "connected"})):
            result = client.session_status()
        self.assertEqual(result["status"], "connected")

    def test_401_raises_auth_error_no_retry(self):
        client = WaxumClient(_cfg())
        calls = []

        def fake_urlopen(*a, **k):
            calls.append(1)
            raise HTTPError("url", 401, "unauthorized", {}, None)

        with patch("waxum_hermes_plugin.client.urlrequest.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(WaxumAuthError):
                client.session_status()
        self.assertEqual(len(calls), 1)

    def test_503_raises_session_unavailable(self):
        client = WaxumClient(_cfg())
        with patch("waxum_hermes_plugin.client.urlrequest.urlopen",
                   side_effect=HTTPError("url", 503, "unavailable", {}, None)):
            with self.assertRaises(WaxumSessionUnavailable):
                client.session_status()

    def test_5xx_retries_then_raises(self):
        client = WaxumClient(_cfg())
        calls = []

        def fake_urlopen(*a, **k):
            calls.append(1)
            raise HTTPError("url", 500, "boom", {}, None)

        with patch("waxum_hermes_plugin.client.urlrequest.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(WaxumRequestError):
                client.session_status()
        self.assertEqual(len(calls), _cfg().max_retries + 1)

    def test_transport_error_retries_then_raises(self):
        client = WaxumClient(_cfg())
        with patch("waxum_hermes_plugin.client.urlrequest.urlopen", side_effect=URLError("dns fail")):
            with self.assertRaises(WaxumRequestError):
                client.session_status()

    def test_list_messages_supports_arrival_cursor(self):
        client = WaxumClient(_cfg())
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return FakeResponse({"messages": []})

        with patch("waxum_hermes_plugin.client.urlrequest.urlopen", side_effect=fake_urlopen):
            result = client.list_messages(limit=50, after=17)

        self.assertEqual(result, {"messages": []})
        self.assertIn("/sessions/s1/messages?limit=50&after=17", captured["url"])

    def test_send_buttons_builds_expected_payload(self):
        client = WaxumClient(_cfg())
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data)
            return FakeResponse({"message_id": "m1"})

        with patch("waxum_hermes_plugin.client.urlrequest.urlopen", side_effect=fake_urlopen):
            result = client.send_buttons("123@s.whatsapp.net", "pick one", [{"id": "a", "text": "A"}])

        self.assertEqual(result["message_id"], "m1")
        self.assertTrue(captured["url"].endswith("/sessions/s1/messages/buttons"))
        self.assertEqual(captured["body"]["to"], "123@s.whatsapp.net")
        # waxum contract: content_text + buttons[{button_id, display_text}]
        self.assertEqual(captured["body"]["content_text"], "pick one")
        self.assertEqual(captured["body"]["buttons"], [{"button_id": "a", "display_text": "A"}])


if __name__ == "__main__":
    unittest.main()
