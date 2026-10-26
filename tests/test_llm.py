import os
import unittest

from loseweight import llm


class TestParseJson(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(llm.parse_json('{"intent": "log_meal"}'), {"intent": "log_meal"})

    def test_code_fence(self):
        self.assertEqual(
            llm.parse_json('```json\n[{"name": "鸡胸肉", "grams": 150}]\n```'),
            [{"name": "鸡胸肉", "grams": 150}],
        )

    def test_surrounding_text(self):
        self.assertEqual(llm.parse_json('好的：{"intent": "help"} 结束'), {"intent": "help"})

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            llm.parse_json("")


class TestNormalizeMealType(unittest.TestCase):
    def test_chinese(self):
        self.assertEqual(llm.normalize_meal_type("早餐"), "breakfast")
        self.assertEqual(llm.normalize_meal_type("午餐"), "lunch")
        self.assertEqual(llm.normalize_meal_type("晚餐"), "dinner")
        self.assertEqual(llm.normalize_meal_type("零食"), "snack")

    def test_english_and_none(self):
        self.assertEqual(llm.normalize_meal_type("lunch"), "lunch")
        self.assertIsNone(llm.normalize_meal_type("下午茶"))
        self.assertIsNone(llm.normalize_meal_type(None))


class TestConfigured(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("DEEPSEEK_API_KEY", None)

    def test_empty_disables(self):
        os.environ["DEEPSEEK_API_KEY"] = ""
        self.assertFalse(llm.is_configured())

    def test_placeholder_disables(self):
        os.environ["DEEPSEEK_API_KEY"] = "disabled"
        self.assertFalse(llm.is_configured())

    def test_key_enables(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test-not-real"
        self.assertTrue(llm.is_configured())


class TestKnownIntents(unittest.TestCase):
    def test_correction_intents_present(self):
        for i in ("undo_meal", "delete_meal", "edit_meal"):
            self.assertIn(i, llm.KNOWN_INTENTS)

    def test_skip_meal_intent_present(self):
        self.assertIn("skip_meal", llm.KNOWN_INTENTS)

    def test_preference_intent_present(self):
        self.assertIn("set_preferences", llm.KNOWN_INTENTS)

    def test_estimated_meal_intent_present(self):
        self.assertIn("log_estimated_meal", llm.KNOWN_INTENTS)


if __name__ == "__main__":
    unittest.main()
