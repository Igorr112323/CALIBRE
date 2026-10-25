from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_grn471_benchmark as checker  # noqa: E402


class BenchmarkCheckerTests(unittest.TestCase):
    def test_exact_numeric_tokens_do_not_accept_prefix_matches(self):
        self.assertFalse(checker.numeric_evidence_check("6", "60 градусов")[0])
        self.assertTrue(checker.numeric_evidence_check("6", "6°")[0])

    def test_decimal_ranges_and_tolerances_are_checked(self):
        self.assertTrue(checker.numeric_evidence_check("5,4", "га/ч 5,4")[0])
        self.assertTrue(checker.numeric_evidence_check("3…6", "снизить скорость до 3…6км/ч")[0])
        self.assertTrue(checker.numeric_evidence_check("4650±50", "4650±50 мм")[0])
        self.assertFalse(checker.numeric_evidence_check("4650±50", "4650 мм")[0])

    def test_spelled_number_is_recognized_in_verbatim_quote(self):
        self.assertTrue(checker.numeric_evidence_check("2", "в течение двух рабочих смен")[0])

    @unittest.skipUnless((ROOT / "tests/test_documents/GRN-471-Kolibri-v4-gold.json").is_file(),
                         "Локальный gold-файл не включается в Git")
    def test_known_image_only_value_is_separated_from_hallucination(self):
        gold = json.loads((ROOT / "tests/test_documents/GRN-471-Kolibri-v4-gold.json").read_text(encoding="utf-8"))
        pages = [""] * 56
        claim = {"value": "800", "unit": "кг", "page": 18, "quote": "Q=800кг"}
        checked = checker.validate_claim_citation(claim, pages, gold)
        self.assertFalse(checked["valid"])
        self.assertTrue(checked["manual_visual_only"])
        fabricated = {"value": "900", "unit": "кг", "page": 18, "quote": "Q=900кг"}
        checked_bad = checker.validate_claim_citation(fabricated, pages, gold)
        self.assertFalse(checked_bad["valid"])
        self.assertFalse(checked_bad["manual_visual_only"])

    @unittest.skipUnless((ROOT / "tests/test_documents/GRN-471-Kolibri-v4-gold.json").is_file(),
                         "Локальный gold-файл не включается в Git")
    def test_visual_only_claim_stays_manual_after_a_machine_verified_claim(self):
        full_gold = json.loads((ROOT / "tests/test_documents/GRN-471-Kolibri-v4-gold.json").read_text(encoding="utf-8"))
        selected = [next(n for n in full_gold["norms"] if n["id"] == norm_id) for norm_id in ("N010", "N035")]
        pages = [""] * 56
        pages[5] = "Транспортный просвет, не менее мм 250"
        claims = [
            {"name": "Транспортный просвет", "value": "250", "unit": "мм", "page": 6,
             "quote": "Транспортный просвет, не менее мм 250"},
            {"name": "Маркировка схемы строповки", "value": "800", "unit": "кг", "page": 18,
             "quote": "Q=800кг"},
        ]
        compared = checker.compare_norms(selected, claims, pages, full_gold)
        self.assertEqual(compared["counts"]["found"], 1)
        self.assertEqual(compared["counts"]["manual_visual_only"], 1)
        self.assertEqual(compared["counts"]["rejected"], 0)
        self.assertEqual(compared["manual_visual_only"][0]["claim"]["value"], "800")

    def test_gold_sources_against_private_pdf_when_available(self):
        pdf = ROOT / "tests/test_documents/GRN-471-Kolibri-v4.pdf"
        if not pdf.exists():
            self.skipTest("Частный PDF не включается в Git; локальная проверка источника пропущена")
        gold = json.loads((ROOT / "tests/test_documents/GRN-471-Kolibri-v4-gold.json").read_text(encoding="utf-8"))
        pages = checker.load_pdf_pages(pdf)
        audit = checker.audit_gold(gold, pages)
        self.assertEqual(audit["counts"]["norms_total"], 63)
        self.assertEqual(audit["counts"]["norms_machine_verified"], 59)
        self.assertEqual(audit["counts"]["norms_manual_visual_or_text_mismatch"], 4)
        self.assertEqual(audit["counts"]["norms_failed"], 0)


if __name__ == "__main__":
    unittest.main()
