from __future__ import annotations

import unittest
from pathlib import Path

from pypdf import PdfReader

from calibre.diagnostics import CASES, is_diagnostic_question, match_manual_diagnosis

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "tests/test_documents/GRN-471-Kolibri-v4.pdf"


class ManualBoundedDiagnosticsTests(unittest.TestCase):
    def test_all_six_documented_fault_patterns_match_and_unknowns_stay_unknown(self):
        questions = {
            "hay-loss-or-windrow": "При работе большие потери сена и слишком широкая ширина валка — почему?",
            "tines-touch-ground": "Граблины задевают за поверхность почвы",
            "transport-orientation": "В транспортном положении грабли развёрнуты относительно оси трактора",
            "gearbox-lubricant-leak": "Наблюдается течь смазки из редуктора",
            "gearbox-metallic-knock": "Возник резкий металлический стук",
            "gearbox-overheating": "Чрезмерный нагрев редуктора",
        }
        self.assertEqual({key for key in questions if match_manual_diagnosis(questions[key])}, set(questions))
        for key, question in questions.items():
            self.assertEqual(match_manual_diagnosis(question)["id"], key)
            self.assertTrue(is_diagnostic_question(question))
        self.assertTrue(is_diagnostic_question("Почему повредился подшипник?"))
        self.assertIsNone(match_manual_diagnosis("Почему повредился подшипник?"))

    @unittest.skipUnless(MANUAL.is_file(), "Локальный исходный PDF не публикуется в Git")
    def test_every_diagnostic_citation_is_present_on_its_claimed_manual_page(self):
        page_texts = [page.extract_text() or "" for page in PdfReader(str(MANUAL)).pages]
        compact = lambda text: "".join(text.split()).casefold()
        for case in CASES:
            self.assertIn(compact(case["quote"]), compact(page_texts[29]), case["id"])
            for source in case.get("manual_sources", []):
                self.assertIn(compact(source["quote"]), compact(page_texts[source["page"] - 1]), case["id"])
        safety = "ЗАПРЕЩАЕТСЯ осматривать, устранять поломки и проводить техническое обслуживание граблей с работающим двигателем трактора."
        self.assertIn(compact(safety), compact(page_texts[12]))


if __name__ == "__main__":
    unittest.main()
