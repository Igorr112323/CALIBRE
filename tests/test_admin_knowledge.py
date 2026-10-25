from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from calibre.models import AnalysisRun, AppSetting, DocumentPage, Machine, Node, Norm, Photo, SourceDocument
from server import create_app

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_MANUAL = ROOT / "tests/test_documents/GRN-471-Kolibri-v4.pdf"


class LocalKnowledgeAdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(
            f"sqlite:///{(Path(self.temp_dir.name) / 'admin.sqlite3').as_posix()}",
            include_demo=False,
            storage_root=Path(self.temp_dir.name) / "store",
        )
        self.transport = httpx.ASGITransport(app=self.app, client=("127.0.0.1", 51000))
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://local")

    async def asyncTearDown(self):
        if hasattr(self, "client"):
            await self.client.aclose()
        if hasattr(self, "app"):
            self.app.state.engine.dispose()
        if hasattr(self, "temp_dir"):
            self.temp_dir.cleanup()

    async def create_machine(self):
        response = await self.client.post("/api/admin/machines", json={
            "code": "GRN-471", "manufacturer": "Kolibri", "model": "ГРН-471", "machine_type": "Грабли"
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    async def upload_manual(self, machine_id: int):
        with PRIVATE_MANUAL.open("rb") as source:
            return await self.client.post("/api/admin/documents", data={"machine_id": str(machine_id)},
                                         files={"file": ("РуководствоПоГраблям.pdf", source, "application/pdf")})

    async def test_admin_can_create_edit_machine_without_uploading_pdf(self):
        created = await self.client.post("/api/admin/machines", json={
            "code": "KOLIBRI-NEW-01", "manufacturer": "Kolibri", "model": "Новая модель",
            "machine_type": "Грабли", "description": "Первичная карточка",
        })
        self.assertEqual(created.status_code, 201, created.text)
        machine_id = created.json()["id"]
        self.assertEqual(created.json()["status"], "draft")

        updated = await self.client.put(f"/api/admin/machines/{machine_id}", json={
            "manufacturer": "Kolibri Pro", "model": "Обновлённая модель",
            "machine_type": "Ворошилка", "description": "Изменённая карточка",
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["model"], "Обновлённая модель")
        machines = await self.client.get("/api/admin/machines")
        self.assertEqual(machines.status_code, 200)
        self.assertEqual(machines.json()[0]["description"], "Изменённая карточка")
        self.assertFalse(machines.json()[0]["has_documents"])

    @unittest.skipUnless(PRIVATE_MANUAL.is_file(), "Частный PDF отсутствует; источник не включается в Git")
    async def test_manual_is_stored_indexed_scoped_and_not_exposed_to_phone(self):
        machine_id = await self.create_machine()
        before_upload = await self.client.post("/api/admin/assistant/answer", json={
            "machine_id": machine_id, "question": "Почему граблины задевают поверхность почвы?"
        })
        self.assertEqual(before_upload.status_code, 200, before_upload.text)
        self.assertEqual(before_upload.json()["status"], "diagnostic_manual_missing")

        uploaded = await self.upload_manual(machine_id)
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        document = uploaded.json()
        analysis = await self.client.get(f"/api/admin/analysis/{document['analysis_run_id']}")
        self.assertEqual(analysis.status_code, 200, analysis.text)
        self.assertIn(analysis.json()["status"], {"queued", "waiting_for_model"})
        self.assertEqual(analysis.json()["result"]["claims"], [])
        self.assertEqual(document["page_count"], 56)
        self.assertEqual(document["scope"], {"guide_start_page": 4, "guide_end_page": 33,
                                              "catalog_start_page": 34, "catalog_end_page": 56})
        self.assertGreater(document["pages_with_text"], 30)

        diagnosis = await self.client.post("/api/admin/assistant/answer", json={
            "machine_id": machine_id, "question": "Почему граблины задевают поверхность почвы?"
        })
        self.assertEqual(diagnosis.status_code, 200, diagnosis.text)
        diagnosis_payload = diagnosis.json()
        self.assertEqual(diagnosis_payload["status"], "manual_diagnosis")
        self.assertFalse(diagnosis_payload["model_used"])
        self.assertIn("Малое расстояние между торцами граблин", diagnosis_payload["answer"])
        self.assertEqual({item["page"] for item in diagnosis_payload["citations"]}, {10, 13, 23, 30})
        self.assertTrue(all(item["viewer_url"].endswith(f"#page={item['page']}")
                            for item in diagnosis_payload["citations"]))

        for question in (
            "При работе большие потери сена и слишком широкий валок",
            "В транспортном положении грабли развёрнуты относительно оси трактора",
            "Течёт смазка из редуктора",
            "Возник резкий металлический стук",
            "Чрезмерный нагрев редуктора",
        ):
            matched = await self.client.post("/api/admin/assistant/answer", json={
                "machine_id": machine_id, "question": question
            })
            self.assertEqual(matched.status_code, 200, matched.text)
            self.assertEqual(matched.json()["status"], "manual_diagnosis", question)
            self.assertTrue(any(item["page"] == 30 for item in matched.json()["citations"]), question)

        unknown_diagnosis = await self.client.post("/api/admin/assistant/answer", json={
            "machine_id": machine_id, "question": "Почему сломался подшипник?"
        })
        self.assertEqual(unknown_diagnosis.status_code, 200, unknown_diagnosis.text)
        self.assertEqual(unknown_diagnosis.json()["status"], "diagnostic_no_match")
        self.assertIsNone(unknown_diagnosis.json()["diagnosis"])

        listing = (await self.client.get("/api/admin/documents", params={"machine_id": machine_id})).json()
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["scope"]["catalog_start_page"], 34)
        pdf = await self.client.get(document["id"] and f"/api/admin/documents/{document['id']}/file")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertEqual(pdf.content[:5], b"%PDF-")
        with Session(self.app.state.engine) as session:
            source_page = session.scalar(select(DocumentPage).where(
                DocumentPage.source_document_id == document["id"], DocumentPage.page_number == 23
            ))
            quote = " ".join(source_page.extracted_text.split()[:8])
        page_image = await self.client.get(
            f"/api/admin/documents/{document['id']}/page/23/image", params={"quote": quote}
        )
        self.assertEqual(page_image.status_code, 200, page_image.text[:300])
        self.assertEqual(page_image.headers["content-type"], "image/png")
        self.assertEqual(page_image.headers["x-calibre-quote-verified"], "1")
        self.assertEqual(page_image.headers["x-calibre-quote-highlighted"], "1")
        self.assertTrue(page_image.content.startswith(b"\x89PNG\r\n\x1a\n"))

        result = await self.client.post("/api/admin/assistant/answer", json={
            "machine_id": machine_id, "question": "Какая разница между концами граблин слева и справа?"
        })
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()
        self.assertEqual(payload["status"], "not_configured")
        self.assertTrue(payload["local_only"])
        pages = [hit["page"] for hit in payload["evidence"]]
        self.assertIn(23, pages)
        self.assertTrue(all(page < 34 for page in pages))
        with Session(self.app.state.engine) as session:
            session.merge(AppSetting(
                key=f"document_embeddings:{document['analysis_run_id']}",
                value='{"model":"bge-m3","pages":[23],"vectors":[[0.1,0.9]]}',
            ))
            session.commit()
        local_status = {"running": True, "chat_model_ready": True}
        model_output = {"answer": quote, "citations": [{
            "document_id": document["id"], "page": 23, "quote": quote,
        }]}
        with patch("server.ollama_status", return_value=local_status), \
             patch("server.embeddings", return_value=[[0.1, 0.9]]), \
             patch("calibre.local_rag.chat_json", return_value=model_output):
            local_answer = await self.client.post("/api/admin/assistant/answer", json={
                "machine_id": machine_id, "question": "Что написано на странице двадцать три руководства?"
            })
        self.assertEqual(local_answer.status_code, 200, local_answer.text)
        self.assertEqual(local_answer.json()["status"], "local_grounded_answer")
        self.assertTrue(local_answer.json()["local_only"])
        self.assertEqual(local_answer.json()["citations"][0]["page"], 23)
        self.assertTrue(local_answer.json()["citations"][0]["viewer_url"].endswith("#page=23"))

        network = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("192.168.1.40", 51001)),
                                    base_url="http://local")
        async with network:
            self.assertEqual((await network.get("/api/admin/documents")).status_code, 403)
            self.assertEqual((await network.get(f"/api/admin/documents/{document['id']}/file")).status_code, 403)
            self.assertEqual((await network.post("/api/admin/assistant/answer", json={
                "machine_id": machine_id, "question": "test"})).status_code, 403)

        duplicate = await self.upload_manual(machine_id)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual((await self.client.get("/api/mobile/catalog")).json()["real_machines"], [])

    @unittest.skipUnless(PRIVATE_MANUAL.is_file(), "Частный PDF отсутствует; источник не включается в Git")
    async def test_review_reject_publish_requires_valid_numeric_evidence(self):
        machine_id = await self.create_machine()
        uploaded = await self.upload_manual(machine_id)
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        document = uploaded.json()
        for _ in range(40):
            progress = await self.client.get(f"/api/admin/analysis/{document['analysis_run_id']}")
            if progress.json()["status"] == "waiting_for_model":
                break
            await asyncio.sleep(0.05)
        self.assertEqual(progress.json()["status"], "waiting_for_model")
        with Session(self.app.state.engine) as session:
            run = session.get(AnalysisRun, document["analysis_run_id"])
            page8 = session.scalar(select(DocumentPage).where(
                DocumentPage.source_document_id == document["id"], DocumentPage.page_number == 8
            ))
            page9 = session.scalar(select(DocumentPage).where(
                DocumentPage.source_document_id == document["id"], DocumentPage.page_number == 9
            ))
            quote8 = " ".join(page8.extracted_text.split()[:12])
            quote9 = " ".join(page9.extracted_text.split()[:12])
            run.status = "review"
            run.stage = "Review test fixture"
            run.raw_response = {"sections": {}, "claims": [
                {"kind": "components", "title": "Проверочный узел", "purpose": "Сведения из руководства",
                 "page": 8, "quote": quote8, "review_status": "auto_verified", "auto_verified": True,
                 "human_verified": False, "validation_errors": []},
                {"kind": "components", "title": "Отклоняемая карточка", "purpose": "Сведения для отказа",
                 "page": 9, "quote": quote9, "review_status": "auto_verified", "auto_verified": True,
                 "human_verified": False, "validation_errors": []},
                {"kind": "technical_norms", "name": "Тестовый зазор", "value": "999", "unit": "мм",
                 "condition": "", "page": 8, "quote": quote8, "review_status": "rejected",
                 "auto_verified": False, "human_verified": False, "validation_errors": ["Тест"]},
            ]}
            session.commit()
        rejected_numeric = await self.client.post(
            f"/api/admin/analysis/{document['analysis_run_id']}/review",
            json={"claim_index": 2, "action": "verify", "reviewer": "Проверяющий", "reason": "Проверено"},
        )
        self.assertEqual(rejected_numeric.status_code, 409, rejected_numeric.text)

        edited = await self.client.post(
            f"/api/admin/analysis/{document['analysis_run_id']}/review",
            json={"claim_index": 0, "action": "edit", "changes": {"title": "Узел после правки"}, "reviewer": "Проверяющий"},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["claim"]["review_status"], "edited")
        verified = await self.client.post(
            f"/api/admin/analysis/{document['analysis_run_id']}/review",
            json={"claim_index": 0, "action": "verify", "reviewer": "Проверяющий"},
        )
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertTrue(verified.json()["claim"]["human_verified"])
        rejected = await self.client.post(
            f"/api/admin/analysis/{document['analysis_run_id']}/review",
            json={"claim_index": 1, "action": "reject", "reason": "Не подтверждено"},
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        published = await self.client.post(
            f"/api/admin/analysis/{document['analysis_run_id']}/publish",
            json={"mode": "human_verified", "reviewer": "Проверяющий"},
        )
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()["counts"]["nodes"], 1)
        self.assertEqual(published.json()["published"], 1)
        with Session(self.app.state.engine) as session:
            run = session.get(AnalysisRun, document["analysis_run_id"])
            raw = dict(run.raw_response)
            claims = list(raw["claims"])
            claims.extend([
                {"kind": "technical_norms", "name": "Одинаковая норма", "value": "5", "unit": "мм",
                 "condition": "одинаковое условие", "page": 8, "quote": quote8,
                 "review_status": "verified", "human_verified": True, "auto_verified": False},
                {"kind": "technical_norms", "name": "Одинаковая норма", "value": "7", "unit": "мм",
                 "condition": "одинаковое условие", "page": 9, "quote": quote9,
                 "review_status": "verified", "human_verified": True, "auto_verified": False},
            ])
            raw["claims"] = claims
            run.raw_response = raw
            session.commit()
        conflict = await self.client.post(
            f"/api/admin/analysis/{document['analysis_run_id']}/publish",
            json={"mode": "human_verified", "reviewer": "Проверяющий"},
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)
        with Session(self.app.state.engine) as session:
            self.assertEqual(session.scalar(select(Norm.id).limit(1)), None)
            nodes = session.scalars(select(Node).where(Node.machine_id == machine_id)).all()
            self.assertEqual([node.title for node in nodes], ["Узел после правки"])

    @unittest.skipUnless(PRIVATE_MANUAL.is_file(), "Частный PDF отсутствует; источник не включается в Git")
    async def test_invalid_pdf_and_bad_page_scopes_are_rejected_without_saved_files(self):
        machine_id = await self.create_machine()
        invalid = await self.client.post("/api/admin/documents", data={"machine_id": str(machine_id)},
                                         files={"file": ("bad.pdf", b"not a pdf", "application/pdf")})
        self.assertEqual(invalid.status_code, 415)
        with PRIVATE_MANUAL.open("rb") as source:
            overlap = await self.client.post("/api/admin/documents", data={
                "machine_id": str(machine_id), "guide_start_page": "4", "guide_end_page": "35",
                "catalog_start_page": "34", "catalog_end_page": "56",
            }, files={"file": ("manual.pdf", source, "application/pdf")})
        self.assertEqual(overlap.status_code, 422)
        self.assertEqual((await self.client.get("/api/admin/documents")).json(), [])

    async def test_uploaded_images_are_validated_and_kept_private(self):
        machine_id = await self.create_machine()
        image_bytes = io.BytesIO()
        Image.new("RGB", (40, 30), color=(20, 90, 50)).save(image_bytes, format="PNG")
        image_bytes.seek(0)
        created = await self.client.post("/api/admin/media", data={"machine_id": str(machine_id),
                                                                    "caption": "Опорный узел, тест"},
                                        files={"file": ("node.png", image_bytes, "image/png")})
        self.assertEqual(created.status_code, 201, created.text)
        record = created.json()
        self.assertEqual(record["caption"], "Опорный узел, тест")
        stored = await self.client.get(record["url"])
        self.assertEqual(stored.status_code, 200)
        self.assertEqual(stored.headers["content-type"], "image/png")
        with Session(self.app.state.engine) as session:
            photo = session.get(Photo, record["id"])
            self.assertEqual(photo.usage_rights, "internal_only")
            self.assertFalse(photo.published)
            self.assertFalse(photo.allow_training)

    async def test_internal_photos_are_opted_in_and_synced_only_over_mobile_lan(self):
        machine_id = await self.create_machine()
        created_node = await self.client.post("/api/admin/nodes", json={
            "machine_id": machine_id, "title": "Тестовый узел", "purpose": "Узел для проверки LAN-фото",
        })
        self.assertEqual(created_node.status_code, 201, created_node.text)
        node_id = created_node.json()["id"]
        with Session(self.app.state.engine) as session:
            session.get(Machine, machine_id).status = "published"
            session.get(Node, node_id).status = "published"
            session.commit()
        image_bytes = io.BytesIO()
        Image.new("RGB", (60, 40), color=(40, 110, 70)).save(image_bytes, format="PNG")
        image_bytes.seek(0)
        upload = await self.client.post("/api/admin/media", data={
            "machine_id": str(machine_id), "node_id": str(node_id), "caption": "Внутренний снимок",
            "sync_to_phones": "true",
        }, files={"file": ("internal.png", image_bytes, "image/png")})
        self.assertEqual(upload.status_code, 201, upload.text)
        photo_id = upload.json()["id"]
        self.assertTrue(upload.json()["sync_to_phones"])
        toggled = await self.client.put(f"/api/admin/media/{photo_id}/sync", json={"enabled": False})
        self.assertEqual(toggled.status_code, 200, toggled.text)
        self.assertFalse(toggled.json()["sync_to_phones"])
        toggled = await self.client.put(f"/api/admin/media/{photo_id}/sync", json={"enabled": True})
        self.assertEqual(toggled.status_code, 200, toggled.text)
        with Session(self.app.state.engine) as session:
            photo = session.get(Photo, photo_id)
            self.assertEqual(photo.usage_rights, "internal_only")
            self.assertFalse(photo.published)
            self.assertTrue(photo.sync_to_phones)

        network = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("192.168.1.40", 51001)),
                                    base_url="http://local")
        async with network:
            catalog = (await network.get("/api/mobile/catalog")).json()
            node = next(node for item in catalog["real_machines"] for node in item["nodes"] if node["id"] == node_id)
            self.assertEqual(node["photos"][0]["url"], f"/media/{photo_id}")
            self.assertTrue(node["photos"][0]["internal_lan_only"])
            mobile_photo = await network.get(f"/api/mobile/media/{photo_id}")
            self.assertEqual(mobile_photo.status_code, 200, mobile_photo.text)
            self.assertEqual(mobile_photo.headers["content-type"], "image/jpeg")
            self.assertEqual(mobile_photo.headers["x-calibre-internal-lan"], "1")
            self.assertEqual((await network.get(f"/api/admin/media/{photo_id}/file")).status_code, 403)
        public_network = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("8.8.8.8", 51002)),
                                           base_url="http://local")
        async with public_network:
            self.assertEqual((await public_network.get(f"/api/mobile/media/{photo_id}")).status_code, 403)

    async def test_machine_codes_are_unique_and_demo_records_cannot_be_used(self):
        machine_id = await self.create_machine()
        duplicate = await self.client.post("/api/admin/machines", json={
            "code": "GRN-471", "manufacturer": "Kolibri", "model": "Ещё одни грабли", "machine_type": "Грабли"
        })
        self.assertEqual(duplicate.status_code, 409)
        with Session(self.app.state.engine) as session:
            session.add(Machine(code="DEMO-ONLY", manufacturer="Kolibri", model="Демо", machine_type="Грабли",
                                is_demo=True, status="published"))
            session.commit()
        if PRIVATE_MANUAL.is_file():
            upload = await self.client.post("/api/admin/documents", data={"machine_id": "2"},
                                            files={"file": ("manual.pdf", PRIVATE_MANUAL.read_bytes(), "application/pdf")})
            self.assertEqual(upload.status_code, 404)


if __name__ == "__main__":
    unittest.main()
