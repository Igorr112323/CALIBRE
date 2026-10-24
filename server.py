"""Локальный сервер КАЛИБР: published-каталог и узкие телефонные очереди."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from calibre.catalog import mobile_catalog
from calibre.database import initialize_database, make_engine
from calibre.models import Adjustment, ExpertQuestion, Feedback, Machine, Node, Problem

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
HOST = "0.0.0.0"
PORT = int(os.environ.get("CALIBRE_PORT", "8765"))


class FeedbackRequest(BaseModel):
    client_event_id: str = Field(min_length=1, max_length=80)
    target_type: Literal["node", "adjustment", "problem"]
    target_id: int = Field(gt=0)
    helpful: bool


class QuestionRequest(BaseModel):
    client_event_id: str = Field(min_length=1, max_length=80)
    machine_id: int | None = Field(default=None, gt=0)
    question: str = Field(min_length=1, max_length=2000)
    context: dict = Field(default_factory=dict)


def is_loopback(address: str | None) -> bool:
    """Проверяет, пришёл ли запрос с того же компьютера."""
    if not address:
        return False
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def local_lan_ip() -> str:
    """Определяет доступный телефону IPv4-адрес без отправки данных в интернет."""
    def usable(address: str) -> bool:
        return not address.startswith(("127.", "169.254."))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        address = sock.getsockname()[0]
        if usable(address):
            return address
    except OSError:
        pass
    finally:
        sock.close()
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = result[4][0]
            if usable(address):
                return address
    except OSError:
        pass
    return "IP-АДРЕС НЕ НАЙДЕН"


def create_app(database_url: str | None = None, include_demo: bool = True) -> FastAPI:
    engine = make_engine(database_url)
    sessions = initialize_database(engine, include_demo=include_demo)
    app = FastAPI(title="КАЛИБР", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine
    app.state.session_factory = sessions

    @app.middleware("http")
    async def local_admin_and_write_guard(request, call_next):
        path = request.url.path
        remote = not is_loopback(request.client.host if request.client else None)
        if remote and (path == "/admin" or path.startswith("/admin/")):
            return JSONResponse(status_code=403, content={
                "ok": False,
                "message": "Админка доступна только на компьютере-сервере",
            })
        phone_submission = request.method == "POST" and path in {
            "/api/mobile/feedback", "/api/mobile/questions"
        }
        if remote and request.method not in {"GET", "HEAD", "OPTIONS"} and not phone_submission:
            return JSONResponse(status_code=403, content={
                "ok": False,
                "message": "Изменение данных с телефона запрещено, кроме специальных очередей приложения",
            })
        return await call_next(request)

    @app.get("/api/mobile/ping")
    async def mobile_ping():
        return {"ok": True, "service": "КАЛИБР", "message": "Связь с сервером есть"}

    @app.get("/api/mobile/catalog")
    async def mobile_get_catalog():
        with Session(app.state.engine) as session:
            return mobile_catalog(session)

    @app.get("/api/mobile/sync")
    async def mobile_sync(revision: str | None = Query(default=None, max_length=128)):
        with Session(app.state.engine) as session:
            catalog = mobile_catalog(session)
        encoded = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        current_revision = hashlib.sha256(encoded).hexdigest()
        changed = revision != current_revision
        return {
            "changed": changed,
            "revision": current_revision,
            "catalog": catalog if changed else None,
        }

    @app.post("/api/mobile/feedback", status_code=202)
    async def mobile_feedback(body: FeedbackRequest):
        with Session(app.state.engine) as session:
            existing = session.scalar(select(Feedback).where(Feedback.client_event_id == body.client_event_id))
            if existing:
                return {"accepted": True, "duplicate": True, "id": existing.id}
            if body.target_type == "node":
                query = select(Node.id).join(Machine).where(
                    Node.id == body.target_id, Node.status == "published", Machine.status == "published"
                )
            elif body.target_type == "adjustment":
                query = select(Adjustment.id).join(Node).join(Machine).where(
                    Adjustment.id == body.target_id, Adjustment.status == "published",
                    Node.status == "published", Machine.status == "published",
                )
            else:
                query = select(Problem.id).join(Machine).where(
                    Problem.id == body.target_id, Problem.status == "published", Machine.status == "published"
                )
            if session.scalar(query) is None:
                raise HTTPException(status_code=404, detail="Карточка не найдена или не опубликована")
            entry = Feedback(
                client_event_id=body.client_event_id,
                target_type=body.target_type,
                target_id=body.target_id,
                helpful=body.helpful,
            )
            session.add(entry)
            session.commit()
            return {"accepted": True, "duplicate": False, "id": entry.id}

    @app.post("/api/mobile/questions", status_code=202)
    async def mobile_question(body: QuestionRequest):
        with Session(app.state.engine) as session:
            existing = session.scalar(select(ExpertQuestion).where(
                ExpertQuestion.client_event_id == body.client_event_id
            ))
            if existing:
                return {"accepted": True, "duplicate": True, "id": existing.id}
            if body.machine_id is not None:
                machine_is_published = session.scalar(select(Machine.id).where(
                    Machine.id == body.machine_id, Machine.status == "published"
                ))
                if machine_is_published is None:
                    raise HTTPException(status_code=404, detail="Машина не найдена или не опубликована")
            entry = ExpertQuestion(
                client_event_id=body.client_event_id,
                machine_id=body.machine_id,
                question=body.question.strip(),
                context_json=body.context,
                status="new",
            )
            if not entry.question:
                raise HTTPException(status_code=422, detail="Введите вопрос")
            session.add(entry)
            session.commit()
            return {"accepted": True, "duplicate": False, "id": entry.id}

    @app.get("/admin", include_in_schema=False)
    async def admin_app():
        return FileResponse(WEB_ROOT / "admin" / "index.html")

    @app.get("/", include_in_schema=False)
    async def no_public_website():
        return JSONResponse(status_code=404, content={
            "ok": False,
            "message": "Публичного сайта нет. Пользовательский интерфейс поставляется в APK.",
        })

    app.mount("/admin/static", StaticFiles(directory=str(WEB_ROOT)), name="admin-static")
    return app


app = create_app()


def main() -> None:
    lan_ip = local_lan_ip()
    print("КАЛИБР запущен. Для остановки закройте это окно.\n", flush=True)
    print(f"Админка (только на этом компьютере): http://127.0.0.1:{PORT}/admin", flush=True)
    if lan_ip == "IP-АДРЕС НЕ НАЙДЕН":
        print("Адрес для приложения на телефоне: не найден — подключите компьютер к Wi-Fi или локальной сети.", flush=True)
    else:
        print(f"Адрес для приложения на телефоне: http://{lan_ip}:{PORT}", flush=True)
        print(f"Проверка связи: http://{lan_ip}:{PORT}/api/mobile/ping", flush=True)
    print("", flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
