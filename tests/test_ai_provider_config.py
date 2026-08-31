import os
import unittest
from unittest.mock import patch

from app import main


class TestAIProviderConfig(unittest.TestCase):
    def test_default_provider_uses_groq_when_key_exists(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "abc123", "AI_PROVIDER": ""}, clear=True):
            self.assertEqual(main.resolve_ai_provider(), "groq")

    def test_production_does_not_use_localhost_ollama_fallback(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "", "GROQ_API_KEY": "", "RENDER": "true"}, clear=True):
            self.assertEqual(main.get_ollama_base_url(), "")

    def test_local_dev_can_use_local_ollama(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "", "GROQ_API_KEY": "", "RENDER": ""}, clear=True):
            self.assertEqual(main.get_ollama_base_url(), "http://localhost:11434")

    @patch("app.main.call_ollama")
    def test_smalltalk_requests_do_not_hit_ai(self, mock_call_ollama):
        result = main.detect_intent("hello there")
        self.assertEqual(result.intent, "chat")
        self.assertIn("hello", result.response.lower())
        mock_call_ollama.assert_not_called()


if __name__ == "__main__":
    unittest.main()
