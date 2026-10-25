from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image
from sqlalchemy.orm import Session

from calibre.models import DocumentPage, Machine, Photo, SourceDocument
from server import create_app

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_MANUAL = ROOT / "tests/test_documents/GRN-471-Kolibri-v4.pdf"


class LocalKnowledgeAdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not PRIVATE_MANUAL.is_file():
            self.skipTest("Частный PDF хранится только в tests/test_documents и не публикуется в Git")
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

    async def test_manual_is_stored_indexed_scoped_and_not_exposed_to_phone(self):
        machine_id = await self.create_machine()
        uploaded = await self.upload_manual(machine_id)
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        document = uploaded.json()
        self.assertEqual(document["page_count"], 56)
        self.assertEqual(document["scope"], {"guide_start_page": 4, "guide_end_page": 33,
                                              "catalog_start_page": 34, "catalog_end_page": 56})
        self.assertGreater(document["pages_with_text"], 30)

        listing = (await self.client.get("/api/admin/documents", params={"machine_id": machine_id})).json()
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["scope"]["catalog_start_page"], 34)
        pdf = await self.client.get(document["id"] and f"/api/admin/documents/{document['id']}/file")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertEqual(pdf.content[:5], b"%PDF-")

        result = await self.client.post("/api/admin/assistant/answer", json={
            "machine_id": machine_id, "question": "Какая разница между концами граблин слева и справа?"
        })
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()
        self.assertEqual(payload["status"], "not_configured")
        pages = [hit["page"] for hit in payload["evidence"]]
        self.assertIn(23, pages)
        self.assertTrue(all(page < 34 for page in pages))

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
        upload = await self.client.post("/api/admin/documents", data={"machine_id": "2"},
                                        files={"file": ("manual.pdf", PRIVATE_MANUAL.read_bytes(), "application/pdf")})
        self.assertEqual(upload.status_code, 404)


if __name__ == "__main__":
    unittest.main()
