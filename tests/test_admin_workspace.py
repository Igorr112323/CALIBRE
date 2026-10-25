from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image
from sqlalchemy.orm import Session

from calibre.models import Machine, Node, Photo
from server import create_app


class SimpleAdminWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(
            f"sqlite:///{(Path(self.temp_dir.name) / 'admin.sqlite3').as_posix()}",
            include_demo=False,
            storage_root=Path(self.temp_dir.name) / "store",
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 51000)),
            base_url="http://local",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.app.state.engine.dispose()
        self.temp_dir.cleanup()

    async def test_machine_card_nodes_and_node_photo_flow(self):
        created = await self.client.post("/api/admin/machines", json={
            "code": "KOLIBRI-GRN-471", "manufacturer": "Kolibri", "model": "ГРН-471",
            "machine_type": "Грабли", "description": "",
        })
        self.assertEqual(created.status_code, 201, created.text)
        machine_id = created.json()["id"]

        updated = await self.client.put(f"/api/admin/machines/{machine_id}", json={
            "manufacturer": "Kolibri", "model": "ГРН-471", "machine_type": "Роторные грабли",
            "description": "Навесная машина",
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["description"], "Навесная машина")

        node = await self.client.post("/api/admin/nodes", json={
            "machine_id": machine_id, "title": "Опорное колесо", "purpose": "Поддерживает раму",
        })
        self.assertEqual(node.status_code, 201, node.text)
        self.assertEqual(node.json()["number"], 1)
        self.assertEqual((await self.client.get("/api/admin/nodes", params={"machine_id": machine_id})).json()[0]["title"], "Опорное колесо")

        image = io.BytesIO()
        Image.new("RGB", (20, 20), color=(25, 90, 50)).save(image, format="PNG")
        image.seek(0)
        uploaded = await self.client.post("/api/admin/media", data={
            "machine_id": str(machine_id), "node_id": str(node.json()["id"]), "caption": "Вид сбоку",
        }, files={"file": ("wheel.png", image, "image/png")})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.assertEqual(uploaded.json()["node_id"], node.json()["id"])
        listed = await self.client.get("/api/admin/media", params={"machine_id": machine_id})
        self.assertEqual(listed.json()[0]["node_id"], node.json()["id"])
        with Session(self.app.state.engine) as session:
            self.assertEqual(session.get(Node, node.json()["id"]).photos[0].caption, "Вид сбоку")
            self.assertFalse(session.get(Photo, uploaded.json()["id"]).published)

        wrong_machine = await self.client.post("/api/admin/machines", json={
            "code": "KOLIBRI-OTHER", "manufacturer": "Kolibri", "model": "Другая",
            "machine_type": "Машина",
        })
        bad_link = await self.client.post("/api/admin/media", data={
            "machine_id": str(wrong_machine.json()["id"]), "node_id": str(node.json()["id"]),
        }, files={"file": ("wrong.png", image.getvalue(), "image/png")})
        self.assertEqual(bad_link.status_code, 422)
        self.assertEqual((await self.client.get("/api/mobile/catalog")).json()["real_machines"], [])


if __name__ == "__main__":
    unittest.main()
