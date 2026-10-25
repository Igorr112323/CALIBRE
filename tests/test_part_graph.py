import json
import tempfile
import unittest
from pathlib import Path

import httpx
from pypdf import PdfReader
from sqlalchemy.orm import Session

from calibre.knowledge import search_part_graph
from calibre.part_graph import build_part_graph, validate_part_graph
from server import create_app

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/test_documents/GRN-471-Kolibri-v4.pdf"


@unittest.skipUnless(SOURCE.is_file(), "local ignored Kolibri v4 PDF is not present")
class GRN471PartGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page_texts = [page.extract_text() or "" for page in PdfReader(str(SOURCE)).pages]
        cls.graph = build_part_graph(SOURCE, cls.page_texts)

    def test_all_124_catalog_positions_and_all_nine_figures_are_accounted_for(self):
        self.assertEqual(self.graph["extraction"]["positions_total"], 124)
        self.assertEqual(self.graph["extraction"]["figures"], list(range(1, 10)))
        self.assertTrue(validate_part_graph(self.graph, self.page_texts)["passed"])
        self.assertEqual(len({item["part_id"] for item in self.graph["parts"]}), 124)

    def test_manual_unknowns_stay_unknown_while_separate_hypotheses_cover_112_positions(self):
        unknown = [item for item in self.graph["parts"]
                   if item["individual_role_status"] == "catalog_membership_only"]
        assembly_only = [item for item in self.graph["parts"]
                         if item["individual_role_status"] == "assembly_role_only"]
        self.assertEqual(len(unknown), 27)
        self.assertEqual(len(assembly_only), 85)
        self.assertEqual(sum(bool(item.get("expert_assessment")) for item in self.graph["parts"]), 112)
        self.assertTrue(all(item["individual_role_id"] is None for item in unknown))
        self.assertTrue(all(item["individual_role_source"] is None for item in unknown))
        self.assertTrue(all("не установлено" in item["individual_role"] for item in unknown))
        self.assertTrue(all(item["expert_assessment"]["status"] == "engineering_hypothesis"
                            and item["expert_assessment"]["manufacturer_confirmed"] is False
                            and item["expert_assessment"]["drawing_pages"] == item["figure_pages"]
                            for item in unknown + assembly_only))

    def test_direct_responsibility_is_quoted_and_page_verified(self):
        tine = next(item for item in self.graph["parts"] if item["designation"] == "ГРП-811.05.03.030")
        self.assertEqual(tine["individual_role_status"], "explicit_or_component_named_in_manual")
        source = tine["individual_role_source"]
        self.assertEqual(source["page"], 7)
        self.assertIn("граблины захватывают скошенную массу", source["quote"].casefold())

    def test_catalog_gaps_are_explicit_not_filled_from_names(self):
        gaps = {item["component"]: item["status"] for item in self.graph["known_catalog_gaps"]}
        self.assertIn("Опора / парковочная стойка", gaps)
        self.assertIn("Распорка транспортная", gaps)
        self.assertIn("no_separate_catalog_position", gaps["Опора / парковочная стойка"])

    async def _upload_and_check_runtime_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "parts.sqlite3"
            app = create_app(f"sqlite:///{db_path.as_posix()}")
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50011))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = await client.post("/api/admin/machines", json={
                    "code": "LOCAL-GRN471", "manufacturer": "Kolibri", "model": "ГРН-471",
                    "machine_type": "Грабли", "description": "локальный тест",
                })
                self.assertEqual(created.status_code, 201, created.text)
                machine_id = created.json()["id"]
                with SOURCE.open("rb") as source:
                    uploaded = await client.post("/api/admin/documents", data={
                        "machine_id": str(machine_id), "title": "локальный тест GRN-471",
                        "guide_start_page": "4", "guide_end_page": "33",
                        "catalog_start_page": "34", "catalog_end_page": "56",
                    }, files={"file": (SOURCE.name, source, "application/pdf")})
                self.assertEqual(uploaded.status_code, 201, uploaded.text)
                self.assertEqual(uploaded.json()["parts_indexed"], 124)

                listed = await client.get("/api/admin/documents", params={"machine_id": machine_id})
                self.assertTrue(listed.json()[0]["parts_indexed"])
                self.assertEqual(listed.json()[0]["catalog_positions"], 124)
                all_parts_response = await client.get("/api/admin/parts", params={"machine_id": machine_id})
                indexed_parts = all_parts_response.json()["documents"][0]["parts"]
                self.assertEqual(len(indexed_parts), 124)
                self.assertEqual(sum(bool(part.get("expert_assessment")) for part in indexed_parts), 112)
                with Session(app.state.engine) as session:
                    for part in self.graph["parts"]:
                        figures = ",".join(map(str, part["figure_refs"]))
                        query = f"рисунок {figures} позиция {part['position']}"
                        hits = search_part_graph(session, query, machine_id=machine_id, limit=2)
                        catalog_hit = next((hit for hit in hits if hit.section == "parts_catalog"), None)
                        self.assertIsNotNone(catalog_hit, query)
                        marker = "STRUCTURED CATALOG INDEX (derived lookup, NOT a verbatim source quote):"
                        found = json.loads(catalog_hit.text.split(marker, 1)[1].lstrip().splitlines()[0])
                        self.assertEqual(found["part_id"], part["part_id"], query)
                        if part["individual_role_status"] != "explicit_or_component_named_in_manual":
                            self.assertEqual(found["expert_assessment"]["status"], "engineering_hypothesis", query)
                            self.assertIs(found["expert_assessment"]["manufacturer_confirmed"], False, query)
                            self.assertTrue(any(hit.section == "parts_drawing" for hit in hits), query)

                response = await client.get("/api/admin/parts", params={
                    "machine_id": machine_id, "q": "ГРН-471.02.060",
                })
                self.assertEqual(response.status_code, 200, response.text)
                graph_doc = response.json()["documents"][0]
                self.assertEqual(len(graph_doc["parts"]), 1)
                part = graph_doc["parts"][0]
                self.assertEqual(part["designation"], "ГРН-471.02.060")
                self.assertEqual(part["individual_role_status"], "assembly_role_only")

                answer = await client.post("/api/admin/assistant/answer", json={
                    "machine_id": machine_id,
                    "question": "Для чего нужна деталь ГРН-471.02.060 и что она делает?",
                })
                self.assertEqual(answer.status_code, 200, answer.text)
                result = answer.json()
                self.assertEqual(result["status"], "part_lookup")
                self.assertFalse(result["model_used"])
                self.assertIn("Отдельную функцию этой детали руководство не устанавливает", result["answer"])
                self.assertTrue(any(hit["section"] == "parts_catalog" for hit in result["evidence"]))
                self.assertTrue(any("assembly_role_only" in hit["text"] for hit in result["evidence"]))
                self.assertGreaterEqual(len(result["citations"]), 2)

                code_only = await client.post("/api/admin/assistant/answer", json={
                    "machine_id": machine_id, "question": "ГРН-471.02.060",
                })
                self.assertEqual(code_only.status_code, 200, code_only.text)
                self.assertTrue(any(hit["section"] == "parts_catalog" for hit in code_only.json()["evidence"]))

                screw = await client.post("/api/admin/assistant/answer", json={
                    "machine_id": machine_id, "question": "ГРН-471.02.090А",
                })
                self.assertEqual(screw.status_code, 200, screw.text)
                self.assertIn("Регулирует расстояние от граблин", screw.json()["answer"])
                self.assertTrue(any(item["page"] == 10 for item in screw.json()["citations"]))

                by_figure = await client.post("/api/admin/assistant/answer", json={
                    "machine_id": machine_id, "question": "рисунок 4 позиция 7",
                })
                self.assertEqual(by_figure.status_code, 200, by_figure.text)
                self.assertIn("ГРН-471.02.090А", by_figure.json()["answer"])

                unknown = await client.post("/api/admin/assistant/answer", json={
                    "machine_id": machine_id, "question": "ГРН-471.00.080",
                })
                self.assertEqual(unknown.status_code, 200, unknown.text)
                self.assertIn("нельзя надёжно сказать", unknown.json()["answer"])
                unknown_citations = unknown.json()["citations"]
                self.assertTrue(any(citation["page"] == 38 and citation["section"] == "parts_catalog"
                                    for citation in unknown_citations))
                self.assertTrue(any(citation["page"] in {36, 37} and citation["section"] == "parts_drawing"
                                    for citation in unknown_citations))
                self.assertIn("Экспертная гипотеза (не подтверждена изготовителем)", unknown.json()["answer"])
                self.assertIn("низкая", unknown.json()["answer"])

                duplicate = await client.post("/api/admin/assistant/answer", json={
                    "machine_id": machine_id, "question": "ГРН-471.05.020",
                })
                self.assertEqual(duplicate.status_code, 200, duplicate.text)
                self.assertIn("несколько каталожных позиций", duplicate.json()["answer"])
                self.assertEqual(len(duplicate.json()["citations"]), 2)
            app.state.engine.dispose()

    def test_local_upload_indexes_graph_and_assistant_retrieval_keeps_unknown_boundary(self):
        import asyncio
        asyncio.run(self._upload_and_check_runtime_index())


if __name__ == "__main__":
    unittest.main()
