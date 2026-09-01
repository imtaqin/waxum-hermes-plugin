from __future__ import annotations

import unittest

from waxum_hermes_plugin.adapter import history_message_to_event_data


class AdapterHistoryTests(unittest.TestCase):
    def test_normal_incoming_message_is_normalized(self):
        data = history_message_to_event_data({
            "message_id": "m1",
            "chat_jid": "123@s.whatsapp.net",
            "sender_jid": "123@s.whatsapp.net",
            "direction": "in",
            "msg_type": "text",
            "body": "hello",
            "push_name": "Alice",
        })

        self.assertEqual(data["message_id"], "m1")
        self.assertEqual(data["chat"], "123@s.whatsapp.net")
        self.assertEqual(data["text"], "hello")
        self.assertFalse(data["is_from_me"])

    def test_self_chat_interactive_response_is_normalized_to_command(self):
        data = history_message_to_event_data({
            "message_id": "m2",
            "chat_jid": "self@lid",
            "sender_jid": "self@lid",
            "direction": "out",
            "msg_type": "interactive_response",
            "body": "🤖 Ganti Model",
        })

        self.assertEqual(data["text"], "/model")
        self.assertFalse(data["is_from_me"])
        self.assertEqual(data["message_type"], "interactive_response")

    def test_all_menu_labels_map_to_commands(self):
        cases = {
            "📊 Status": "/status",
            "🔄 Reset": "/reset",
            "🎙️ Voice Mode": "/voice on",
            "🔇 Voice Off": "/voice off",
            "📦 Compress": "/compress",
            "🧠 Memory": "/memory",
            "📋 Context Info": "/ctx",
            "❓ Help": "/help",
            "🔌 Waxum Status": "/waxum-status",
        }
        for label, command in cases.items():
            with self.subTest(label=label):
                data = history_message_to_event_data({
                    "message_id": "m-" + command.replace("/", "").replace(" ", "-"),
                    "chat_jid": "self@lid",
                    "sender_jid": "self@lid",
                    "direction": "out",
                    "msg_type": "interactive_response",
                    "body": label,
                })
                self.assertEqual(data["text"], command)

    def test_outgoing_normal_message_is_treated_as_self_chat_input(self):
        data = history_message_to_event_data({
            "message_id": "m3",
            "chat_jid": "self@lid",
            "sender_jid": "self@lid",
            "direction": "out",
            "msg_type": "text",
            "body": "hello from my other device",
        })
        self.assertEqual(data["text"], "hello from my other device")
        self.assertFalse(data["is_from_me"])

    def test_known_bot_outgoing_message_is_ignored(self):
        self.assertIsNone(history_message_to_event_data({
            "message_id": "m3",
            "chat_jid": "self@lid",
            "sender_jid": "self@lid",
            "direction": "out",
            "msg_type": "text",
            "body": "bot reply",
        }, own_message_ids={"m3"}))

    def test_outgoing_interactive_response_in_other_chat_is_ignored(self):
        self.assertIsNone(history_message_to_event_data({
            "message_id": "m4",
            "chat_jid": "group@g.us",
            "sender_jid": "bot@lid",
            "direction": "out",
            "msg_type": "interactive_response",
            "body": "🤖 Ganti Model",
        }))


if __name__ == "__main__":
    unittest.main()
