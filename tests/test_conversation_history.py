import inspect
import unittest

from ogurec.cogs.conversation_cog import ConversationCog, extra_system_for_reply
from ogurec.cogs.presence_game_cog import PresenceGameCog


class SpeakerHistoryTests(unittest.TestCase):
    def setUp(self):
        self.cog = ConversationCog.__new__(ConversationCog)
        self.cog.conversation_history = {}
        self.cog.reset_tasks = {}
        self.cog._update_channel_activity = lambda _channel_id: None

    def test_two_speakers_stay_in_message_content(self):
        """Модель часто игнорирует поле name — авторы должны быть в тексте реплик."""
        self.cog._add_user_message(1, "кто в доту?", "Егор")
        self.cog._add_user_message(1, "я пас", "Рома")

        payload = [
            {key: value for key, value in message.items() if key != "name"}
            for message in self.cog._get_channel_history(1)
        ]
        contents = [message["content"] for message in payload]

        self.assertIn("Егор: кто в доту?", contents)
        self.assertIn("Рома: я пас", contents)
        self.assertNotEqual(contents[0], contents[1])

    def test_assistant_reply_stays_on_the_person_it_answered(self):
        self.cog._add_user_message(1, "кто в доту?", "Егор")
        self.cog._add_assistant_message(1, "после твоего мида я лучше посплю", reply_to="Егор")
        self.cog._add_user_message(1, "я пас", "Рома")

        contents = [message["content"] for message in self.cog._get_channel_history(1)]
        self.assertEqual(
            contents,
            [
                "Егор: кто в доту?",
                "Ты (Егор): после твоего мида я лучше посплю",
                "Рома: я пас",
            ],
        )

    def test_on_message_stores_display_name_not_login(self):
        source = inspect.getsource(ConversationCog.on_message)
        self.assertIn("display_name", source)
        self.assertIn("_add_user_message", source)

    def test_jump_in_prompt_describes_labeled_history(self):
        text = extra_system_for_reply(random_phrase=True, author_info="Рома", mentioned_users_info="")
        self.assertIn("Ты (Имя):", text)
        self.assertNotIn("не спорь", text.lower())

    def test_addressed_reply_facts_are_only_about_that_person(self):
        text = extra_system_for_reply(
            random_phrase=False,
            author_info="Рома Skadi (никнейм: roma)",
            mentioned_users_info="",
        )
        self.assertIn("Рома Skadi", text)
        self.assertIn("только про него", text)
        self.assertNotIn("не спорь", text.lower())


class PresenceHistoryTests(unittest.TestCase):
    def test_steam_hours_are_not_injected_into_chat_history(self):
        source = inspect.getsource(PresenceGameCog)
        self.assertNotIn("add_assistant_message", source)
