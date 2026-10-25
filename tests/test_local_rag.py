from __future__ import annotations

import unittest
from unittest.mock import patch

from calibre.knowledge import EvidenceHit
from calibre.local_rag import generate_local_answer


HIT = EvidenceHit(
    document_id=7, document_title="Руководство", filename="manual.pdf", page=12,
    score=1.0, coverage=1.0, text="Зазор в соединении установлен 4 мм.",
    section="manual", extraction_method="text", text_quality="good",
    citation_text="Зазор в соединении установлен 4 мм.",
)


class LocalRagGuardTests(unittest.TestCase):
    @patch("calibre.local_rag.chat_json")
    def test_answer_requires_an_exact_local_quote_and_supported_number(self, chat):
        chat.return_value = {
            "answer": "Зазор установлен 4 мм.",
            "citations": [{"document_id": 7, "page": 12, "quote": "Зазор в соединении установлен 4 мм."}],
        }
        result = generate_local_answer("Какой зазор?", [HIT], "qwen3:8b")
        self.assertEqual(result["status"], "local_grounded_answer")
        self.assertTrue(result["local_only"])
        self.assertEqual(result["citations"][0]["quote"], HIT.text)
        chat.assert_called_once()

    @patch("calibre.local_rag.chat_json")
    def test_hallucinated_number_is_blocked_even_when_quote_is_valid(self, chat):
        chat.return_value = {
            "answer": "Зазор установлен 5 мм.",
            "citations": [{"document_id": 7, "page": 12, "quote": "Зазор в соединении установлен 4 мм."}],
        }
        result = generate_local_answer("Какой зазор?", [HIT], "qwen3:8b")
        self.assertEqual(result["status"], "source_check_failed")
        self.assertEqual(result["unsupported_numbers"], ["5"])

    @patch("calibre.local_rag.chat_json")
    def test_model_cannot_cite_a_quote_missing_from_the_retrieved_page(self, chat):
        chat.return_value = {
            "answer": "Зазор установлен 4 мм.",
            "citations": [{"document_id": 7, "page": 12, "quote": "На странице написано 4 мм."}],
        }
        result = generate_local_answer("Какой зазор?", [HIT], "qwen3:8b")
        self.assertEqual(result["status"], "source_check_failed")
        self.assertEqual(result["citations"], [])

    @patch("calibre.local_rag.chat_json", side_effect=RuntimeError("offline"))
    def test_model_error_does_not_fall_back_to_cloud_or_fabricate_answer(self, chat):
        result = generate_local_answer("Какой зазор?", [HIT], "qwen3:8b")
        self.assertEqual(result["status"], "local_model_error")
        self.assertTrue(result["local_only"])
        self.assertFalse(result["model_used"])
        chat.assert_called_once()


if __name__ == "__main__":
    unittest.main()
