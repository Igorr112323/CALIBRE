import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from calibre.models import Adjustment, ExpertQuestion, Feedback, Machine, Node, Norm, SourceDocument
from server import create_app, is_loopback


class ServerStageTwoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.app = create_app(f"sqlite:///{db_path.as_posix()}")
        self.transport = httpx.ASGITransport(app=self.app, client=("127.0.0.1", 50000))
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.app.state.engine.dispose()
        self.temp_dir.cleanup()

    def network_client(self):
        transport = httpx.ASGITransport(app=self.app, client=("192.168.1.50", 50001))
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    def test_only_loopback_is_admin_origin(self):
        self.assertTrue(is_loopback("127.0.0.1"))
        self.assertTrue(is_loopback("::1"))
        self.assertFalse(is_loopback("192.168.1.12"))
        self.assertFalse(is_loopback("10.0.0.4"))
        self.assertFalse(is_loopback(None))
        self.assertFalse(is_loopback("not-an-ip"))

    async def test_phone_ping_returns_service_status(self):
        response = await self.client.get("/api/mobile/ping")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "КАЛИБР")

    async def test_public_user_website_is_not_served(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 404)
        self.assertIn("нет", response.text)
        self.assertEqual((await self.client.get("/app")).status_code, 404)

    async def test_admin_page_is_available_from_localhost(self):
        response = await self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Kolibri — панель знаний", response.text)
        self.assertIn('id="admin-home"', response.text)
        self.assertIn('id="home-create-machine"', response.text)
        self.assertIn('id="admin-machine-list"', response.text)
        self.assertIn('id="machine-create-form"', response.text)
        self.assertIn('id="new-machine-photos"', response.text)
        self.assertIn('id="machine-publish-toggle"', response.text)
        self.assertIn("Сделать карточку", response.text)
        self.assertNotIn('id="local-ai-panel"', response.text)
        self.assertNotIn("Ollama", response.text)
        self.assertIn('id="manual-question-form"', response.text)
        self.assertNotIn("ollama-chat-model", response.text)
        self.assertNotIn('id="homeSearch"', response.text)
        self.assertIn('id="card-form"', response.text)
        self.assertIn('id="manual-upload-form"', response.text)
        self.assertEqual((await self.client.get("/admin/static/admin/admin.css")).status_code, 200)
        admin_js = await self.client.get("/admin/static/admin/admin.js")
        self.assertEqual(admin_js.status_code, 200)
        self.assertIn("/api/admin/machines", admin_js.text)
        self.assertIn("/api/admin/documents", admin_js.text)

    async def test_pc_server_serves_admin_but_never_a_phone_interface(self):
        self.assertEqual((await self.client.get("/preview/")).status_code, 404)
        self.assertEqual((await self.client.get("/app")).status_code, 404)
        async with self.network_client() as network:
            self.assertEqual((await network.get("/preview/")).status_code, 404)
            self.assertEqual((await network.get("/api/mobile/ping")).status_code, 200)

    async def test_local_pdf_viewer_renders_and_highlights_page_without_vendor_cdn(self):
        viewer = await self.client.get("/admin/static/admin/pdf-viewer.html")
        script = await self.client.get("/admin/static/admin/pdf-viewer.js")
        self.assertEqual(viewer.status_code, 200)
        self.assertIn("pdf-viewer.js", viewer.text)
        self.assertIn("page-image", viewer.text)
        self.assertNotIn("https://", viewer.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn("локальной базы Kolibri", script.text)
        self.assertIn("/page/${page}/image", script.text)
        self.assertNotIn("pdf.min.mjs", script.text)

    async def test_network_client_is_denied_admin_but_can_ping(self):
        async with self.network_client() as network:
            response = await network.get("/admin")
            self.assertEqual(response.status_code, 403)
            self.assertIn("только на компьютере", response.text)
            self.assertEqual((await network.get("/admin/static/shared.css")).status_code, 403)
            self.assertEqual((await network.get("/api/mobile/ping")).status_code, 200)

    async def test_phone_cannot_mutate_data(self):
        async with self.network_client() as network:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                with self.subTest(method=method):
                    response = await network.request(method, "/api/mobile/catalog")
                    self.assertEqual(response.status_code, 403)
                    self.assertIn("запрещено", response.text)

    async def test_database_migration_and_demo_seed_are_repeatable(self):
        db_url = f"sqlite:///{(Path(self.temp_dir.name) / 'repeat.sqlite3').as_posix()}"
        first = create_app(db_url)
        second = create_app(db_url)
        try:
            with Session(second.state.engine) as session:
                self.assertEqual(session.query(Machine).filter(Machine.is_demo.is_(True)).count(), 1)
        finally:
            first.state.engine.dispose()
            second.state.engine.dispose()

    async def test_sync_returns_catalog_once_then_revision_not_modified(self):
        first = await self.client.get("/api/mobile/sync")
        self.assertEqual(first.status_code, 200)
        payload = first.json()
        self.assertTrue(payload["changed"])
        self.assertEqual(len(payload["catalog"]["demo_machines"]), 1)
        demo = payload["catalog"]["demo_machines"][0]
        self.assertEqual(demo["model"], "ГРН-471 Kolibri (демо)")
        self.assertEqual(len(demo["nodes"]), 12)
        self.assertEqual(len(demo["problems"]), 6)
        self.assertEqual(sum(len(adjustment["norms"]) for node in demo["nodes"] for adjustment in node["adjustments"]), 59)
        second = await self.client.get("/api/mobile/sync", params={"revision": payload["revision"]})
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["changed"])
        self.assertIsNone(second.json()["catalog"])

    async def test_demo_catalog_is_grounded_and_marks_auto_verification(self):
        payload = (await self.client.get("/api/mobile/catalog")).json()
        demo = payload["demo_machines"][0]
        self.assertEqual(demo["model"], "ГРН-471 Kolibri (демо)")
        self.assertIn("автопроверено", demo["label"])
        self.assertIn("человеком не сверено", demo["label"])
        self.assertEqual(len(demo["nodes"]), 12)
        self.assertEqual(len(demo["problems"]), 6)
        norms = [norm for node in demo["nodes"] for adj in node["adjustments"] for norm in adj["norms"]]
        self.assertEqual(len(norms), 59)
        self.assertTrue(all(norm["auto_verified"] and not norm["human_verified"] for norm in norms))
        self.assertTrue(all(norm["source"]["page_start"] and norm["source"]["quote"] for norm in norms))
        self.assertTrue(all(node["source_page"] and node["source_quote"] for node in demo["nodes"]))
        self.assertTrue(any("абсолютное" in item["value"].casefold() for item in demo["characteristics"] if "не указано" in item["label"].casefold()))
        self.assertFalse(any("DEMO-ONLY" in str(item) for item in demo["nodes"]))

    async def test_phone_can_submit_only_intended_feedback_and_question(self):
        catalog = (await self.client.get("/api/mobile/catalog")).json()
        demo = catalog["demo_machines"][0]
        node_id = demo["nodes"][0]["id"]
        problem_id = demo["problems"][0]["id"]
        async with self.network_client() as network:
            feedback = {"client_event_id": "feedback-event-1", "target_type": "node", "target_id": node_id, "helpful": True}
            accepted = await network.post("/api/mobile/feedback", json=feedback)
            self.assertEqual(accepted.status_code, 202)
            self.assertTrue(accepted.json()["accepted"])
            duplicate = await network.post("/api/mobile/feedback", json=feedback)
            self.assertEqual(duplicate.status_code, 202)
            self.assertTrue(duplicate.json()["duplicate"])
            question = {"client_event_id": "question-event-1", "machine_id": demo["id"],
                        "question": "Уточните источник карточки", "context": {"problem_id": problem_id}}
            sent_question = await network.post("/api/mobile/questions", json=question)
            self.assertEqual(sent_question.status_code, 202)
            repeated_question = await network.post("/api/mobile/questions", json=question)
            self.assertTrue(repeated_question.json()["duplicate"])
            blocked = await network.post("/api/mobile/catalog", json={})
            self.assertEqual(blocked.status_code, 403)
            invalid = await network.post("/api/mobile/feedback", json={**feedback, "client_event_id": "invalid", "target_id": 987654})
            self.assertEqual(invalid.status_code, 404)
        with Session(self.app.state.engine) as session:
            self.assertEqual(session.query(Feedback).count(), 1)
            self.assertEqual(session.query(ExpertQuestion).count(), 1)
            self.assertEqual(session.query(ExpertQuestion).one().question, "Уточните источник карточки")

    async def test_phone_receives_only_published_content_and_demo_is_separate(self):
        with Session(self.app.state.engine) as session:
            source = SourceDocument(
                machine=Machine(code="CLAAS-TEST", manufacturer="CLAAS", model="ТЕСТ", machine_type="Косилка",
                                status="published", version=2, is_demo=False),
                title="Тестовый источник", original_filename="manual.pdf", sha256="a" * 64,
                storage_path="documents/manual.pdf",
            )
            real_machine = source.machine
            visible = Node(number=1, title="Опубликованный узел", purpose="Видимая карточка", status="published")
            visible.adjustments.append(Adjustment(
                title="Проверенная регулировка", instructions=["Проверенный шаг"], tool="Ключ", status="published",
                norms=[
                    Norm(value_original="12–18", unit_original="мм", source_kind="document",
                         source_document=source, source_page_start=2, source_page_end=2,
                         source_quote="12–18 mm", review_status="verified"),
                    Norm(value_original="250", unit_original="Н", source_kind="department_measurement",
                         measured_by="Тестовый эксперт", measured_at=date(2026, 10, 24),
                         review_status="unverified"),
                ],
            ))
            hidden = Node(number=2, title="СЕКРЕТНЫЙ ЧЕРНОВИК", purpose="Не выдавать", status="ai_draft")
            real_machine.nodes.extend([visible, hidden])
            hidden_machine = Machine(code="DRAFT-ONLY", manufacturer="ДЕМО", model="Черновик",
                                     machine_type="Тест", status="draft", is_demo=False)
            session.add_all([source, hidden_machine])
            session.commit()

        response = await self.client.get("/api/mobile/catalog")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["demo_machines"]), 1)
        self.assertTrue(payload["demo_machines"][0]["is_demo"])
        self.assertIn("автопроверено", payload["demo_machines"][0]["label"])
        self.assertEqual([m["code"] for m in payload["real_machines"]], ["CLAAS-TEST"])
        real = payload["real_machines"][0]
        self.assertEqual([n["title"] for n in real["nodes"]], ["Опубликованный узел"])
        norms = real["nodes"][0]["adjustments"][0]["norms"]
        self.assertEqual([n["value"] for n in norms], ["12–18"])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("СЕКРЕТНЫЙ ЧЕРНОВИК", serialized)
        self.assertNotIn("DRAFT-ONLY", serialized)
        self.assertNotIn('"value": "250"', json.dumps(real, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
