from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image
from sqlalchemy import select
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

    async def test_photo_markers_remain_draft_until_owner_publishes_machine(self):
        created = await self.client.post("/api/admin/machines", json={
            "code": "KOLIBRI-MARKED", "manufacturer": "Kolibri", "model": "Маркированная машина",
            "machine_type": "Сельхозтехника", "description": "",
        })
        self.assertEqual(created.status_code, 201, created.text)
        machine_id = created.json()["id"]
        image = io.BytesIO()
        Image.new("RGB", (32, 24), color=(45, 90, 45)).save(image, format="PNG")
        image.seek(0)
        uploaded = await self.client.post("/api/admin/media", data={
            "machine_id": str(machine_id), "caption": "Общий вид", "photo_kind": "machine_scheme",
            "sync_to_phones": "true",
        }, files={"file": ("machine.png", image, "image/png")})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        photo_id = uploaded.json()["id"]

        marker = await self.client.post("/api/admin/nodes", json={
            "machine_id": machine_id, "title": "Опорное колесо", "purpose": "",
            "map_x": 0.27, "map_y": 0.64, "marker_photo_id": photo_id,
        })
        self.assertEqual(marker.status_code, 201, marker.text)
        unmarked = await self.client.post("/api/admin/nodes", json={
            "machine_id": machine_id, "title": "Непроверенный узел", "purpose": "",
        })
        self.assertEqual(unmarked.status_code, 201, unmarked.text)
        self.assertEqual((await self.client.get("/api/mobile/catalog")).json()["real_machines"], [])

        listing = await self.client.get("/api/admin/nodes", params={"machine_id": machine_id})
        self.assertEqual(listing.json()[0]["marker_photo_id"], photo_id)
        self.assertAlmostEqual(listing.json()[0]["map_x"], 0.27, places=5)
        published = await self.client.put(f"/api/admin/machines/{machine_id}/publication", json={"published": True})
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()["status"], "published")
        with Session(self.app.state.engine) as session:
            nodes = session.scalars(select(Node).where(Node.machine_id == machine_id).order_by(Node.number)).all()
            self.assertEqual(nodes[0].status, "published")
            self.assertEqual(nodes[1].status, "review")
        mobile = (await self.client.get("/api/mobile/catalog")).json()["real_machines"]
        self.assertEqual(len(mobile), 1)
        self.assertEqual(mobile[0]["nodes"][0]["map"], {"x": 0.27, "y": 0.64})
        self.assertEqual(mobile[0]["nodes"][0]["photos"][0]["url"], f"/media/{photo_id}")
        self.assertTrue(mobile[0]["nodes"][0]["photos"][0]["internal_lan_only"])
        mobile_photo = await self.client.get(f"/api/mobile/media/{photo_id}")
        self.assertEqual(mobile_photo.status_code, 200, mobile_photo.text)
        self.assertEqual(mobile_photo.headers["content-type"], "image/jpeg")

        edited = await self.client.put(f"/api/admin/nodes/{marker.json()['id']}", json={
            "title": "Колесо опорное", "purpose": "Поддерживает раму",
            "map_x": 0.31, "map_y": 0.62, "marker_photo_id": photo_id,
        })
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["machine_status"], "draft")
        self.assertEqual((await self.client.get("/api/mobile/catalog")).json()["real_machines"], [])
        republished = await self.client.put(f"/api/admin/machines/{machine_id}/publication", json={"published": True})
        self.assertEqual(republished.status_code, 200, republished.text)
        mobile_after_edit = (await self.client.get("/api/mobile/catalog")).json()["real_machines"]
        self.assertEqual(mobile_after_edit[0]["nodes"][0]["title"], "Колесо опорное")

        unpublished = await self.client.put(f"/api/admin/machines/{machine_id}/publication", json={"published": False})
        self.assertEqual(unpublished.status_code, 200, unpublished.text)
        self.assertEqual(unpublished.json()["status"], "draft")
        self.assertEqual((await self.client.get("/api/mobile/catalog")).json()["real_machines"], [])

    async def test_problem_cards_can_be_edited_and_require_republication(self):
        created = await self.client.post("/api/admin/machines", json={
            "code": "KOLIBRI-PROBLEMS", "manufacturer": "Kolibri", "model": "Проверочная машина",
            "machine_type": "Сельхозтехника", "description": "",
        })
        machine_id = created.json()["id"]
        problem = await self.client.post("/api/admin/problems", json={
            "machine_id": machine_id,
            "title": "Машина не включается",
            "symptom": "Нет движения рабочих органов",
            "steps": ["Проверьте подключение", "Осмотрите привод"],
        })
        self.assertEqual(problem.status_code, 201, problem.text)
        listed = await self.client.get("/api/admin/problems", params={"machine_id": machine_id})
        self.assertEqual(listed.json()[0]["steps"], ["Проверьте подключение", "Осмотрите привод"])
        published = await self.client.put(f"/api/admin/machines/{machine_id}/publication", json={"published": True})
        self.assertEqual(published.status_code, 200, published.text)
        mobile = (await self.client.get("/api/mobile/catalog")).json()["real_machines"]
        self.assertEqual(mobile[0]["problems"][0]["title"], "Машина не включается")
        edited = await self.client.put(f"/api/admin/problems/{problem.json()['id']}", json={
            "title": "Привод не вращается",
            "symptom": "Рабочие органы остаются неподвижны",
            "steps": ["Проверьте привод", "Проверьте соединение"],
        })
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["machine_status"], "draft")
        self.assertEqual((await self.client.get("/api/mobile/catalog")).json()["real_machines"], [])
        republished = await self.client.put(f"/api/admin/machines/{machine_id}/publication", json={"published": True})
        self.assertEqual(republished.status_code, 200, republished.text)
        mobile = (await self.client.get("/api/mobile/catalog")).json()["real_machines"]
        self.assertEqual(mobile[0]["problems"][0]["steps"][0]["instruction"], "Проверьте привод")

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
        first_publish = await self.client.put(f"/api/admin/machines/{machine_id}/publication", json={"published": True})
        self.assertEqual(first_publish.status_code, 200, first_publish.text)
        edited_after_publish = await self.client.put(f"/api/admin/machines/{machine_id}", json={
            "manufacturer": "Kolibri", "model": "ГРН-471", "machine_type": "Роторные грабли",
            "description": "Описание изменено после проверки",
        })
        self.assertEqual(edited_after_publish.status_code, 200, edited_after_publish.text)
        self.assertEqual(edited_after_publish.json()["status"], "draft")

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
