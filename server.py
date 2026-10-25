"""Локальный сервер КАЛИБР: published-каталог и узкие телефонные очереди."""
from __future__ import annotations

import hashlib
import ipaddress
from datetime import datetime
import io
import json
import os
import re
import socket
import unicodedata
import uuid
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from calibre.assistant import _part_lookup_response
from calibre.diagnostics import (diagnostic_answer, exact_manual_for_machine,
                                 is_diagnostic_question, match_manual_diagnosis)
from calibre.catalog import mobile_catalog
from calibre.database import DATA_ROOT, initialize_database, make_engine
from calibre.document_ai import (analysis_payload, enqueue_analysis, recover_analysis_jobs,
                                 _verify_record)
from calibre.knowledge import EvidenceHit, is_parts_query, normalize, search_pages, search_part_graph, validate_document_scope
from calibre.local_rag import generate_local_answer
from calibre.ollama_local import (cosine_similarity, embeddings, model_settings, ollama_status,
                                 save_model_settings, start_model_download)
from calibre.part_graph import CATALOG_SHA256, build_part_graph
from calibre.models import (Adjustment, AnalysisRun, AppSetting, DocumentPage, ExpertQuestion,
                            Feedback, Machine, MachineCharacteristic, Node, Norm, Photo,
                            Problem, ProblemStep, SourceDocument)

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


class MachineCreate(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    manufacturer: str = Field(min_length=1, max_length=160)
    model: str = Field(min_length=1, max_length=160)
    machine_type: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)


class MachineUpdate(BaseModel):
    manufacturer: str = Field(min_length=1, max_length=160)
    model: str = Field(min_length=1, max_length=160)
    machine_type: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)


class MachinePublicationRequest(BaseModel):
    published: bool


class NodeCreate(BaseModel):
    machine_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=240)
    purpose: str = Field(default="", max_length=4000)
    map_x: float | None = Field(default=None, ge=0, le=1)
    map_y: float | None = Field(default=None, ge=0, le=1)
    marker_photo_id: int | None = Field(default=None, gt=0)


class NodeUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    purpose: str = Field(default="", max_length=4000)
    map_x: float | None = Field(default=None, ge=0, le=1)
    map_y: float | None = Field(default=None, ge=0, le=1)
    marker_photo_id: int | None = Field(default=None, gt=0)


class AssistantRequest(BaseModel):
    machine_id: int = Field(gt=0)
    question: str = Field(min_length=1, max_length=2000)


class OllamaSettingsRequest(BaseModel):
    chat_model: str = Field(min_length=1, max_length=120)
    search_model: str = Field(min_length=1, max_length=120)


class OllamaDownloadRequest(BaseModel):
    profile: Literal["chat", "search"]


class AnalysisReviewRequest(BaseModel):
    claim_index: int = Field(ge=0)
    action: Literal["verify", "reject", "edit"]
    reviewer: str = Field(default="", max_length=240)
    reason: str = Field(default="", max_length=1000)
    changes: dict = Field(default_factory=dict)


class AnalysisPublishRequest(BaseModel):
    mode: Literal["auto_verified", "human_verified"]
    reviewer: str = Field(default="", max_length=240)


class PhotoSyncRequest(BaseModel):
    enabled: bool


MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_PDF_PAGES = 1200


async def stage_upload(upload: UploadFile, directory: Path, max_bytes: int) -> tuple[Path, str, int]:
    directory.mkdir(parents=True, exist_ok=True)
    staged = directory / f".upload-{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    size = 0
    try:
        with staged.open("wb") as target:
            while True:
                block = await upload.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail="Файл превышает допустимый размер")
                digest.update(block)
                target.write(block)
        if size == 0:
            raise HTTPException(status_code=422, detail="Файл пустой")
        return staged, digest.hexdigest(), size
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def stored_file_path(data_root: Path, relative_path: str) -> Path:
    root = data_root.resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(status_code=404, detail="Файл не найден")
    return path


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


def create_app(database_url: str | None = None, include_demo: bool = True,
               storage_root: str | Path | None = None, recover_analysis: bool = True) -> FastAPI:
    engine = make_engine(database_url)
    sessions = initialize_database(engine, include_demo=include_demo)
    app = FastAPI(title="КАЛИБР", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine
    app.state.session_factory = sessions
    app.state.data_root = Path(storage_root).resolve() if storage_root is not None else DATA_ROOT.resolve()
    if recover_analysis:
        recover_analysis_jobs(app)

    @app.middleware("http")
    async def local_admin_and_write_guard(request, call_next):
        path = request.url.path
        remote = not is_loopback(request.client.host if request.client else None)
        if remote and (
            path == "/admin" or path.startswith("/admin/")
            or path == "/api/admin" or path.startswith("/api/admin/")
        ):
            return JSONResponse(status_code=403, content={
                "ok": False,
                "message": "Админка, документы и локальная обработка доступны только на компьютере-сервере",
            })
        if remote and path.startswith("/api/mobile/media/"):
            try:
                lan_client = ipaddress.ip_address(request.client.host if request.client else "").is_private
            except ValueError:
                lan_client = False
            if not lan_client:
                return JSONResponse(status_code=403, content={
                    "ok": False, "message": "Внутренние фото доступны только в локальной сети",
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

    def document_scope(session: Session, document_id: int) -> dict:
        setting = session.get(AppSetting, f"document_scope:{document_id}")
        if not setting:
            return {}
        try:
            value = json.loads(setting.value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def semantic_manual_hits(session: Session, machine_id: int, question: str, search_model: str) -> list[EvidenceHit]:
        """Use only local guide-page vectors; catalog pages never enter norm retrieval."""
        runs = session.scalars(select(AnalysisRun).where(
            AnalysisRun.machine_id == machine_id,
            AnalysisRun.source_document_id.is_not(None),
        ).order_by(AnalysisRun.started_at.desc())).all()
        indexes = []
        for run in runs:
            setting = session.get(AppSetting, f"document_embeddings:{run.id}")
            if not setting:
                continue
            try:
                index = json.loads(setting.value)
                if index.get("model") != search_model:
                    continue
                indexes.append((run, index))
            except (TypeError, json.JSONDecodeError):
                continue
        if not indexes:
            return []
        try:
            query_vector = embeddings(search_model, [question], timeout=90.0)[0]
        except Exception:
            return []
        ranked = []
        for run, index in indexes:
            document = session.get(SourceDocument, run.source_document_id)
            if not document:
                continue
            page_numbers = index.get("pages", [])
            vectors = index.get("vectors", [])
            for page_number, vector in zip(page_numbers, vectors):
                score = cosine_similarity(query_vector, vector)
                if score <= 0:
                    continue
                page = session.scalar(select(DocumentPage).where(
                    DocumentPage.source_document_id == document.id,
                    DocumentPage.page_number == int(page_number),
                ))
                if page is None or not page.extracted_text.strip():
                    continue
                scope = document_scope(session, document.id)
                start_page = scope.get("guide_start_page")
                end_page = scope.get("guide_end_page")
                if isinstance(start_page, int) and page.page_number < start_page:
                    continue
                if isinstance(end_page, int) and page.page_number > end_page:
                    continue
                ranked.append(EvidenceHit(
                    document_id=document.id, document_title=document.title,
                    filename=document.original_filename, page=page.page_number,
                    score=score * 2.5, coverage=score,
                    text=page.extracted_text, section="manual",
                    extraction_method=page.extraction_method, text_quality=page.text_quality,
                    citation_text=page.extracted_text,
                ))
        ranked.sort(key=lambda hit: hit.score, reverse=True)
        return ranked[:6]

    @app.get("/api/admin/stats")
    async def admin_stats():
        with Session(app.state.engine) as session:
            real_machines = session.scalars(select(Machine).where(Machine.is_demo.is_(False))).all()
            document_count = len(session.scalars(select(SourceDocument.id)).all())
            page_count = len(session.scalars(select(DocumentPage.id)).all())
            photo_count = len(session.scalars(select(Photo.id)).all())
            questions = len(session.scalars(select(ExpertQuestion.id).where(ExpertQuestion.status == "new")).all())
            pending = len(session.scalars(select(Node.id).where(Node.status.in_(("ai_draft", "review")))).all())
            local_ai = ollama_status(session)
        return {"machines": len(real_machines), "documents": document_count, "pages": page_count,
                "images": photo_count, "new_questions": questions, "draft_nodes": pending,
                "ai": local_ai}

    @app.get("/api/admin/machines")
    async def admin_machines():
        with Session(app.state.engine) as session:
            machines = session.scalars(select(Machine).where(Machine.is_demo.is_(False)).order_by(Machine.model)).all()
            result = []
            for machine in machines:
                document_count = session.scalar(select(SourceDocument.id).where(
                    SourceDocument.machine_id == machine.id).limit(1))
                result.append({"id": machine.id, "code": machine.code, "manufacturer": machine.manufacturer,
                               "model": machine.model, "machine_type": machine.machine_type,
                               "description": machine.description, "status": machine.status,
                               "has_documents": document_count is not None})
            return result

    @app.post("/api/admin/machines", status_code=201)
    async def admin_create_machine(body: MachineCreate):
        machine = Machine(code=body.code.strip(), manufacturer=body.manufacturer.strip(), model=body.model.strip(),
                          machine_type=body.machine_type.strip(), description=body.description.strip(), status="draft")
        if not all((machine.code, machine.manufacturer, machine.model, machine.machine_type)):
            raise HTTPException(status_code=422, detail="Заполните код, марку, модель и тип техники")
        with Session(app.state.engine) as session:
            session.add(machine)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HTTPException(status_code=409, detail="Техника с таким кодом уже есть")
            return {"id": machine.id, "code": machine.code, "manufacturer": machine.manufacturer,
                    "model": machine.model, "machine_type": machine.machine_type, "status": machine.status}

    @app.put("/api/admin/machines/{machine_id}")
    async def admin_update_machine(machine_id: int, body: MachineUpdate):
        with Session(app.state.engine) as session:
            machine = session.get(Machine, machine_id)
            if machine is None or machine.is_demo or machine.status == "archived":
                raise HTTPException(status_code=404, detail="Рабочая техника не найдена")
            machine.manufacturer = body.manufacturer.strip()
            machine.model = body.model.strip()
            machine.machine_type = body.machine_type.strip()
            machine.description = body.description.strip()
            if machine.status == "published":
                machine.status = "draft"
            session.commit()
            return {"id": machine.id, "code": machine.code, "manufacturer": machine.manufacturer,
                    "model": machine.model, "machine_type": machine.machine_type,
                    "description": machine.description, "status": machine.status}

    @app.put("/api/admin/machines/{machine_id}/publication")
    async def admin_set_machine_publication(machine_id: int, body: MachinePublicationRequest):
        with Session(app.state.engine) as session:
            machine = session.get(Machine, machine_id)
            if machine is None or machine.is_demo or machine.status == "archived":
                raise HTTPException(status_code=404, detail="Рабочая техника не найдена")
            machine.status = "published" if body.published else "draft"
            if body.published:
                marker_nodes = session.scalars(select(Node).where(
                    Node.machine_id == machine_id,
                    Node.marker_photo_id.is_not(None),
                    Node.status.in_(("review", "verified")),
                )).all()
                for node in marker_nodes:
                    node.status = "published"
            session.commit()
            return {"id": machine.id, "status": machine.status,
                    "published": machine.status == "published"}

    @app.get("/api/admin/nodes")
    async def admin_nodes(machine_id: int = Query(gt=0)):
        with Session(app.state.engine) as session:
            machine = session.get(Machine, machine_id)
            if machine is None or machine.is_demo:
                raise HTTPException(status_code=404, detail="Рабочая техника не найдена")
            nodes = session.scalars(select(Node).where(Node.machine_id == machine_id).order_by(Node.number)).all()
            return [{"id": node.id, "number": node.number, "title": node.title,
                     "purpose": node.purpose, "status": node.status,
                     "map_x": float(node.map_x) if node.map_x is not None else None,
                     "map_y": float(node.map_y) if node.map_y is not None else None,
                     "marker_photo_id": node.marker_photo_id} for node in nodes]

    @app.post("/api/admin/nodes", status_code=201)
    async def admin_create_node(body: NodeCreate):
        if (body.map_x is None) != (body.map_y is None):
            raise HTTPException(status_code=422, detail="Для отметки укажите обе координаты фото")
        if body.marker_photo_id is not None and (body.map_x is None or body.map_y is None):
            raise HTTPException(status_code=422, detail="Для узла на фото нужны координаты")
        with Session(app.state.engine) as session:
            machine = session.get(Machine, body.machine_id)
            if machine is None or machine.is_demo or machine.status == "archived":
                raise HTTPException(status_code=404, detail="Рабочая техника не найдена")
            marker_photo = None
            if body.marker_photo_id is not None:
                marker_photo = session.get(Photo, body.marker_photo_id)
                if marker_photo is None or marker_photo.machine_id != body.machine_id:
                    raise HTTPException(status_code=422, detail="Фото отметки не относится к этой технике")
            max_number = session.scalar(select(Node.number).where(Node.machine_id == body.machine_id)
                                        .order_by(Node.number.desc()).limit(1)) or 0
            node = Node(machine_id=body.machine_id, number=max_number + 1,
                        title=body.title.strip(), purpose=body.purpose.strip(), status="review",
                        map_x=body.map_x, map_y=body.map_y,
                        marker_photo_id=marker_photo.id if marker_photo else None)
            session.add(node)
            session.commit()
            return {"id": node.id, "number": node.number, "title": node.title,
                    "purpose": node.purpose, "status": node.status,
                    "map_x": float(node.map_x) if node.map_x is not None else None,
                    "map_y": float(node.map_y) if node.map_y is not None else None,
                    "marker_photo_id": node.marker_photo_id}

    @app.put("/api/admin/nodes/{node_id}")
    async def admin_update_node(node_id: int, body: NodeUpdate):
        if (body.map_x is None) != (body.map_y is None):
            raise HTTPException(status_code=422, detail="Для отметки укажите обе координаты фото")
        if body.marker_photo_id is not None and (body.map_x is None or body.map_y is None):
            raise HTTPException(status_code=422, detail="Для узла на фото нужны координаты")
        with Session(app.state.engine) as session:
            node = session.get(Node, node_id)
            if node is None:
                raise HTTPException(status_code=404, detail="Узел не найден")
            machine = session.get(Machine, node.machine_id)
            if machine is None or machine.is_demo or machine.status == "archived":
                raise HTTPException(status_code=404, detail="Рабочая техника не найдена")
            marker_photo = None
            if body.marker_photo_id is not None:
                marker_photo = session.get(Photo, body.marker_photo_id)
                if marker_photo is None or marker_photo.machine_id != node.machine_id:
                    raise HTTPException(status_code=422, detail="Фото отметки не относится к этой технике")
            node.title = body.title.strip()
            node.purpose = body.purpose.strip()
            node.map_x = body.map_x
            node.map_y = body.map_y
            node.marker_photo_id = marker_photo.id if marker_photo else None
            node.status = "review"
            if machine.status == "published":
                machine.status = "draft"
            session.commit()
            return {"id": node.id, "number": node.number, "title": node.title,
                    "purpose": node.purpose, "status": node.status,
                    "map_x": float(node.map_x) if node.map_x is not None else None,
                    "map_y": float(node.map_y) if node.map_y is not None else None,
                    "marker_photo_id": node.marker_photo_id, "machine_status": machine.status}

    @app.get("/api/admin/documents")
    async def admin_documents(machine_id: int | None = Query(default=None, gt=0)):
        with Session(app.state.engine) as session:
            stmt = select(SourceDocument).join(Machine).where(Machine.is_demo.is_(False)).order_by(SourceDocument.uploaded_at.desc())
            if machine_id is not None:
                stmt = stmt.where(SourceDocument.machine_id == machine_id)
            documents = session.scalars(stmt).all()
            result = []
            for doc in documents:
                part_setting = session.get(AppSetting, f"part_graph:{doc.id}")
                try:
                    part_count = len(json.loads(part_setting.value).get("parts", [])) if part_setting else 0
                except (TypeError, json.JSONDecodeError, AttributeError):
                    part_count = 0
                result.append({"id": doc.id, "machine_id": doc.machine_id, "title": doc.title,
                               "filename": doc.original_filename, "sha256": doc.sha256,
                               "page_count": doc.page_count, "uploaded_at": doc.uploaded_at.isoformat(),
                               "scope": document_scope(session, doc.id),
                               "pages_indexed": session.scalar(select(DocumentPage.id).where(
                                   DocumentPage.source_document_id == doc.id).limit(1)) is not None,
                               "parts_indexed": part_count > 0, "catalog_positions": part_count,
                               "url": f"/api/admin/documents/{doc.id}/file"})
            return result

    @app.get("/api/admin/parts")
    async def admin_parts(machine_id: int = Query(gt=0), q: str = Query(default="", max_length=200),
                          limit: int = Query(default=250, ge=1, le=500)):
        with Session(app.state.engine) as session:
            machine = session.get(Machine, machine_id)
            if machine is None or machine.is_demo or machine.status == "archived":
                raise HTTPException(status_code=404, detail="Рабочая техника не найдена")
            documents = session.scalars(select(SourceDocument).where(
                SourceDocument.machine_id == machine_id).order_by(SourceDocument.uploaded_at.desc())).all()
            response = []
            query = q.casefold().strip()
            for document in documents:
                setting = session.get(AppSetting, f"part_graph:{document.id}")
                if not setting:
                    continue
                try:
                    graph = json.loads(setting.value)
                except (TypeError, json.JSONDecodeError):
                    continue
                parts = graph.get("parts", [])
                if query:
                    parts = [part for part in parts if query in " ".join(str(part.get(key) or "") for key in
                              ("designation", "name", "assembly", "part_id", "note")).casefold()]
                response.append({"document_id": document.id, "title": document.title,
                                 "filename": document.original_filename, "sha256": document.sha256,
                                 "record_status": graph.get("record_status"),
                                 "source_scope": graph.get("source_scope"),
                                 "extraction": graph.get("extraction"),
                                 "assemblies": graph.get("assemblies", []),
                                 "manual_components": graph.get("manual_components", []),
                                 "known_catalog_gaps": graph.get("known_catalog_gaps", []),
                                 "parts": parts[:limit]})
            return {"machine_id": machine_id, "documents": response}

    @app.post("/api/admin/documents", status_code=201)
    async def admin_upload_document(
        machine_id: int = Form(gt=0),
        file: UploadFile = File(...),
        title: str = Form(default="", max_length=300),
        guide_start_page: int | None = Form(default=None, ge=1),
        guide_end_page: int | None = Form(default=None, ge=1),
        catalog_start_page: int | None = Form(default=None, ge=1),
        catalog_end_page: int | None = Form(default=None, ge=1),
    ):
        filename = Path((file.filename or "").replace("\\", "/")).name.strip()
        if not filename or Path(filename).suffix.casefold() != ".pdf":
            await file.close()
            raise HTTPException(status_code=415, detail="Загрузите файл PDF")
        staging_dir = app.state.data_root / "staging"
        staged, sha256, _size = await stage_upload(file, staging_dir, MAX_PDF_BYTES)
        try:
            with staged.open("rb") as source:
                if source.read(5) != b"%PDF-":
                    raise HTTPException(status_code=415, detail="Файл не похож на корректный PDF")
            reader = PdfReader(str(staged), strict=False)
            if reader.is_encrypted:
                try:
                    unlocked = reader.decrypt("")
                except Exception:
                    unlocked = 0
                if not unlocked:
                    raise HTTPException(status_code=422, detail="Защищённый паролем PDF не поддерживается")
            page_count = len(reader.pages)
            if page_count < 1 or page_count > MAX_PDF_PAGES:
                raise HTTPException(status_code=422, detail=f"Допустимо от 1 до {MAX_PDF_PAGES} страниц")
            extracted_pages: list[tuple[int, str, str]] = []
            for index, page in enumerate(reader.pages, start=1):
                try:
                    text = (page.extract_text() or "").strip()
                except Exception:
                    text = ""
                quality = "empty" if not text else ("low" if len(text) < 40 else "good")
                extracted_pages.append((index, text, quality))
            try:
                reader.stream.close()
            except (AttributeError, OSError):
                pass
            try:
                scope = validate_document_scope(page_count, guide_start_page, guide_end_page,
                                                catalog_start_page, catalog_end_page)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if not scope and sha256 == CATALOG_SHA256 and page_count == 56:
                scope = {"guide_start_page": 4, "guide_end_page": 33,
                         "catalog_start_page": 34, "catalog_end_page": 56}
            part_graph = None
            if sha256 == CATALOG_SHA256 and page_count == 56:
                part_graph = build_part_graph(staged, [text for _index, text, _quality in extracted_pages], sha256)
            safe_name = filename[:300]
            relative = Path("documents") / str(machine_id) / f"{sha256}.pdf"
            destination = stored_file_path(app.state.data_root, relative.as_posix())
            destination.parent.mkdir(parents=True, exist_ok=True)
            with Session(app.state.engine) as session:
                machine = session.get(Machine, machine_id)
                if machine is None or machine.is_demo or machine.status == "archived":
                    raise HTTPException(status_code=404, detail="Рабочая техника не найдена")
                existing = session.scalar(select(SourceDocument).where(
                    SourceDocument.machine_id == machine_id, SourceDocument.sha256 == sha256))
                if existing:
                    raise HTTPException(status_code=409, detail=f"Этот PDF уже загружен (документ №{existing.id})")
                staged.replace(destination)
                document = SourceDocument(machine_id=machine_id, title=title.strip() or Path(safe_name).stem,
                                          original_filename=safe_name, sha256=sha256, page_count=page_count,
                                          storage_path=relative.as_posix(), language="ru")
                session.add(document)
                session.flush()
                for index, text, quality in extracted_pages:
                    session.add(DocumentPage(source_document_id=document.id, page_number=index,
                                              extracted_text=text, extraction_method="text" if text else "none",
                                              text_quality=quality))
                if scope:
                    session.add(AppSetting(key=f"document_scope:{document.id}",
                                           value=json.dumps(scope, ensure_ascii=False, sort_keys=True)))
                if part_graph:
                    session.add(AppSetting(key=f"part_graph:{document.id}",
                                           value=json.dumps(part_graph, ensure_ascii=False, sort_keys=True)))
                analysis_run = AnalysisRun(
                    machine_id=machine_id, source_document_id=document.id,
                    prompt_version="grn471-local-extract-v1", stage="queued",
                    progress_percent=0, status="queued",
                    raw_response={"sections": {}, "claims": [], "rejected": []},
                )
                session.add(analysis_run)
                session.flush()
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    destination.unlink(missing_ok=True)
                    raise HTTPException(status_code=409, detail="PDF не удалось сохранить: повторная загрузка")
                enqueue_analysis(app, analysis_run.id)
                return {"id": document.id, "machine_id": machine_id, "title": document.title,
                        "filename": safe_name, "page_count": page_count,
                        "pages_with_text": sum(1 for page in session.scalars(select(DocumentPage).where(
                            DocumentPage.source_document_id == document.id)).all() if page.text_quality == "good"),
                        "scope": scope, "sha256": sha256,
                        "analysis_run_id": analysis_run.id, "analysis_status": "queued",
                        "parts_indexed": len(part_graph.get("parts", [])) if part_graph else 0}
        except HTTPException:
            staged.unlink(missing_ok=True)
            raise
        except Exception as exc:
            staged.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="PDF не удалось прочитать. Проверьте файл и попробуйте ещё раз.") from exc

    @app.get("/api/admin/documents/{document_id}/file")
    async def admin_document_file(document_id: int):
        with Session(app.state.engine) as session:
            document = session.get(SourceDocument, document_id)
            if document is None:
                raise HTTPException(status_code=404, detail="Документ не найден")
            path = stored_file_path(app.state.data_root, document.storage_path)
            if not path.is_file():
                raise HTTPException(status_code=404, detail="PDF отсутствует в хранилище")
            return FileResponse(path, media_type="application/pdf", filename=document.original_filename,
                                content_disposition_type="inline")

    @app.get("/api/admin/documents/{document_id}/page/{page_number}/image")
    async def admin_document_page_image(document_id: int, page_number: int, quote: str = Query(default="", max_length=2000)):
        with Session(app.state.engine) as session:
            document = session.get(SourceDocument, document_id)
            if document is None:
                raise HTTPException(status_code=404, detail="Документ не найден")
            page_record = session.scalar(select(DocumentPage).where(
                DocumentPage.source_document_id == document_id,
                DocumentPage.page_number == page_number,
            ))
            if page_record is None:
                raise HTTPException(status_code=404, detail="Страница не найдена в локальном индексе")
            pdf_path = stored_file_path(app.state.data_root, document.storage_path)
            if not pdf_path.is_file():
                raise HTTPException(status_code=404, detail="PDF отсутствует в локальном хранилище")
            verified_quote = bool(quote and normalize(quote) in normalize(page_record.extracted_text or ""))
        try:
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(str(pdf_path))
            if page_number < 1 or page_number > len(pdf):
                pdf.close()
                raise HTTPException(status_code=404, detail="Страница отсутствует в PDF")
            page = pdf[page_number - 1]
            bitmap = page.render(scale=1.6)
            image = bitmap.to_pil().convert("RGBA")
            highlighted = False
            if verified_quote:
                text_page = page.get_textpage()
                page_text = text_page.get_text_range()
                mapped_chars = []
                char_indexes = []
                for char_index, char in enumerate(page_text):
                    normalized_char = unicodedata.normalize("NFKC", char).casefold().replace("ё", "е")
                    for part in normalized_char:
                        if part.isspace():
                            if mapped_chars and mapped_chars[-1] != " ":
                                mapped_chars.append(" ")
                                char_indexes.append(char_index)
                        else:
                            mapped_chars.append(part)
                            char_indexes.append(char_index)
                full_normalized_page = "".join(mapped_chars)
                leading = len(full_normalized_page) - len(full_normalized_page.lstrip())
                trailing = len(full_normalized_page.rstrip())
                normalized_page = full_normalized_page[leading:trailing]
                char_indexes = char_indexes[leading:trailing]
                normalized_quote = normalize(quote)
                start_index = normalized_page.find(normalized_quote)
                if start_index >= 0 and normalized_quote:
                    char_start = char_indexes[start_index]
                    char_end = char_indexes[start_index + len(normalized_quote) - 1]
                    width, height = page.get_size()
                    scale_x, scale_y = image.width / width, image.height / height
                    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
                    from PIL import ImageDraw
                    draw = ImageDraw.Draw(overlay)
                    for text_index in range(char_start, char_end + 1):
                        try:
                            left, bottom, right, top = text_page.get_charbox(text_index)
                        except (IndexError, TypeError, ValueError):
                            continue
                        box = (max(0, int(left * scale_x) - 1),
                               max(0, int((height - top) * scale_y) - 1),
                               min(image.width, int(right * scale_x) + 2),
                               min(image.height, int((height - bottom) * scale_y) + 2))
                        if box[2] > box[0] and box[3] > box[1]:
                            draw.rectangle(box, fill=(255, 228, 45, 105), outline=(255, 175, 0, 190), width=1)
                    image = Image.alpha_composite(image, overlay)
                    highlighted = True
                text_page.close()
            output = io.BytesIO()
            image.convert("RGB").save(output, format="PNG", optimize=True)
            bitmap.close()
            page.close()
            pdf.close()
            return Response(content=output.getvalue(), media_type="image/png", headers={
                "Cache-Control": "no-store", "X-Calibre-Quote-Verified": "1" if verified_quote else "0",
                "X-Calibre-Quote-Highlighted": "1" if highlighted else "0",
            })
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Не удалось отрисовать страницу PDF: {type(exc).__name__}") from exc

    @app.delete("/api/admin/documents/{document_id}", status_code=204)
    async def admin_delete_document(document_id: int):
        with Session(app.state.engine) as session:
            document = session.get(SourceDocument, document_id)
            if document is None:
                raise HTTPException(status_code=404, detail="Документ не найден")
            if session.scalar(select(Norm.id).where(Norm.source_document_id == document_id).limit(1)) is not None:
                raise HTTPException(status_code=409, detail="Документ указан источником нормы; сначала снимите связанные записи с публикации")
            path = stored_file_path(app.state.data_root, document.storage_path)
            for key in (f"document_scope:{document_id}", f"part_graph:{document_id}"):
                setting = session.get(AppSetting, key)
                if setting is not None:
                    session.delete(setting)
            session.delete(document)
            session.commit()
            path.unlink(missing_ok=True)
        return Response(status_code=204)

    @app.get("/api/admin/media")
    async def admin_media(machine_id: int | None = Query(default=None, gt=0)):
        with Session(app.state.engine) as session:
            stmt = select(Photo).join(Machine).where(Machine.is_demo.is_(False)).order_by(Photo.id.desc())
            if machine_id is not None:
                stmt = stmt.where(Photo.machine_id == machine_id)
            photos = session.scalars(stmt).all()
            return [{"id": photo.id, "machine_id": photo.machine_id, "node_id": photo.node_id,
                     "caption": photo.caption, "kind": photo.photo_kind,
                     "sync_to_phones": photo.sync_to_phones,
                     "filename": Path(photo.storage_path).name,
                     "url": f"/api/admin/media/{photo.id}/file"} for photo in photos]

    @app.post("/api/admin/media", status_code=201)
    async def admin_upload_media(
        machine_id: int = Form(gt=0),
        file: UploadFile = File(...),
        caption: str = Form(default="", max_length=300),
        node_id: int | None = Form(default=None, gt=0),
        sync_to_phones: bool = Form(default=False),
        photo_kind: Literal["candidate", "machine_scheme"] = Form(default="candidate"),
    ):
        staging_dir = app.state.data_root / "staging"
        staged, sha256, size = await stage_upload(file, staging_dir, MAX_IMAGE_BYTES)
        try:
            try:
                with Image.open(staged) as image:
                    image.verify()
                with Image.open(staged) as image:
                    image_format = (image.format or "").upper()
                    width, height = image.size
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                raise HTTPException(status_code=415, detail="Файл не является поддерживаемым изображением") from exc
            extensions = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
            if image_format not in extensions or width * height > 40_000_000:
                raise HTTPException(status_code=415, detail="Поддерживаются JPG, PNG и WebP размером до 40 Мп")
            relative = Path("media") / str(machine_id) / f"{sha256}{extensions[image_format]}"
            destination = stored_file_path(app.state.data_root, relative.as_posix())
            destination.parent.mkdir(parents=True, exist_ok=True)
            with Session(app.state.engine) as session:
                machine = session.get(Machine, machine_id)
                if machine is None or machine.is_demo or machine.status == "archived":
                    raise HTTPException(status_code=404, detail="Рабочая техника не найдена")
                if node_id is not None:
                    node = session.get(Node, node_id)
                    if node is None or node.machine_id != machine_id:
                        raise HTTPException(status_code=422, detail="Выбранный узел не относится к этой технике")
                if sync_to_phones and node_id is None and photo_kind != "machine_scheme":
                    raise HTTPException(status_code=422, detail="Для передачи фото по LAN прикрепите его к узлу")
                duplicate = session.scalar(select(Photo).where(Photo.machine_id == machine_id,
                                                               Photo.storage_path == relative.as_posix()))
                if duplicate:
                    raise HTTPException(status_code=409, detail="Это изображение уже загружено")
                staged.replace(destination)
                photo = Photo(machine_id=machine_id, node_id=node_id, storage_path=relative.as_posix(),
                              caption=caption.strip(), photo_kind="node" if node_id else photo_kind,
                              usage_rights="internal_only", allow_training=False, published=False,
                              sync_to_phones=sync_to_phones)
                session.add(photo)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    destination.unlink(missing_ok=True)
                    raise HTTPException(status_code=409, detail="Изображение не удалось сохранить")
                return {"id": photo.id, "machine_id": machine_id, "node_id": photo.node_id,
                        "caption": photo.caption, "filename": destination.name, "bytes": size,
                        "sync_to_phones": photo.sync_to_phones,
                        "url": f"/api/admin/media/{photo.id}/file"}
        except HTTPException:
            staged.unlink(missing_ok=True)
            raise
        except Exception as exc:
            staged.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="Не удалось загрузить изображение") from exc

    @app.put("/api/admin/media/{photo_id}/sync")
    async def admin_update_media_sync(photo_id: int, body: PhotoSyncRequest):
        with Session(app.state.engine) as session:
            photo = session.get(Photo, photo_id)
            if photo is None:
                raise HTTPException(status_code=404, detail="Изображение не найдено")
            marker_exists = session.scalar(select(Node.id).where(
                Node.marker_photo_id == photo_id, Node.status == "published"
            ).limit(1)) is not None
            if body.enabled and (photo.usage_rights != "internal_only" or
                                 (photo.node_id is None and (photo.photo_kind != "machine_scheme" or not marker_exists))):
                raise HTTPException(status_code=409, detail="LAN-sync включается только для внутреннего фото с опубликованным узлом")
            photo.sync_to_phones = body.enabled
            session.commit()
            return {"id": photo.id, "sync_to_phones": photo.sync_to_phones}

    @app.get("/api/admin/media/{photo_id}/file")
    async def admin_media_file(photo_id: int):
        with Session(app.state.engine) as session:
            photo = session.get(Photo, photo_id)
            if photo is None:
                raise HTTPException(status_code=404, detail="Изображение не найдено")
            path = stored_file_path(app.state.data_root, photo.storage_path)
            if not path.is_file():
                raise HTTPException(status_code=404, detail="Изображение отсутствует в хранилище")
            return FileResponse(path)

    @app.get("/api/mobile/media/{photo_id}")
    async def mobile_internal_media(photo_id: int):
        with Session(app.state.engine) as session:
            photo = session.get(Photo, photo_id)
            if photo is None or not photo.sync_to_phones or photo.usage_rights != "internal_only":
                raise HTTPException(status_code=404, detail="Внутреннее изображение не разрешено для локальной синхронизации")
            machine = session.get(Machine, photo.machine_id)
            if machine is None or machine.status != "published":
                raise HTTPException(status_code=404, detail="Изображение доступно только в опубликованной карточке")
            if photo.photo_kind == "machine_scheme":
                published_marker = session.scalar(select(Node.id).where(
                    Node.machine_id == photo.machine_id, Node.marker_photo_id == photo.id,
                    Node.status == "published",
                ).limit(1))
                if published_marker is None:
                    raise HTTPException(status_code=404, detail="Схема доступна только с опубликованной отметкой")
            else:
                node = session.get(Node, photo.node_id) if photo.node_id is not None else None
                if node is None or node.status != "published":
                    raise HTTPException(status_code=404, detail="Изображение доступно только в опубликованном узле")
            path = stored_file_path(app.state.data_root, photo.storage_path)
            if not path.is_file():
                raise HTTPException(status_code=404, detail="Изображение отсутствует в локальном хранилище")
        try:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=78, optimize=True)
            return Response(content=output.getvalue(), media_type="image/jpeg", headers={
                "Cache-Control": "private, no-store", "X-Calibre-Internal-LAN": "1",
            })
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise HTTPException(status_code=422, detail="Не удалось подготовить внутреннее изображение") from exc

    @app.get("/api/admin/ollama/status")
    async def admin_ollama_status():
        with Session(app.state.engine) as session:
            return ollama_status(session)

    @app.put("/api/admin/ollama/settings")
    async def admin_ollama_settings(body: OllamaSettingsRequest):
        with Session(app.state.engine) as session:
            try:
                selected = save_model_settings(session, body.chat_model, body.search_model)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {**selected, "status": ollama_status(session)}

    @app.post("/api/admin/ollama/download", status_code=202)
    async def admin_ollama_download(body: OllamaDownloadRequest):
        with Session(app.state.engine) as session:
            selected = model_settings(session)
        model = selected["chat_model"] if body.profile == "chat" else selected["search_model"]
        return start_model_download(app.state.session_factory, model)

    @app.get("/api/admin/analysis")
    async def admin_analysis_list(machine_id: int = Query(gt=0)):
        with Session(app.state.engine) as session:
            runs = session.scalars(select(AnalysisRun).where(
                AnalysisRun.machine_id == machine_id
            ).order_by(AnalysisRun.started_at.desc())).all()
            return [analysis_payload(run) for run in runs]

    @app.get("/api/admin/analysis/{run_id}")
    async def admin_analysis_get(run_id: int):
        with Session(app.state.engine) as session:
            run = session.get(AnalysisRun, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Разбор документа не найден")
            return analysis_payload(run)

    @app.post("/api/admin/analysis/{run_id}/resume", status_code=202)
    async def admin_analysis_resume(run_id: int):
        with Session(app.state.engine) as session:
            run = session.get(AnalysisRun, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Разбор документа не найден")
            if run.status in {"running", "indexing"}:
                return {"accepted": True, "already_running": True, **analysis_payload(run)}
            run.status = "queued"
            run.stage = "Возобновление с последнего сохранённого раздела"
            session.commit()
        enqueue_analysis(app, run_id)
        return {"accepted": True, "already_running": False, "id": run_id}

    @app.post("/api/admin/analysis/{run_id}/review")
    async def admin_analysis_review(run_id: int, body: AnalysisReviewRequest):
        with Session(app.state.engine) as session:
            run = session.get(AnalysisRun, run_id)
            if run is None or run.source_document_id is None:
                raise HTTPException(status_code=404, detail="Разбор документа не найден")
            if run.status not in {"review", "error"}:
                raise HTTPException(status_code=409, detail="Карточки можно проверять после завершения разбора или его остановки.")
            document = session.get(SourceDocument, run.source_document_id)
            pages = {row.page_number: row.extracted_text for row in session.scalars(select(DocumentPage).where(
                DocumentPage.source_document_id == run.source_document_id
            )).all()}
            raw = dict(run.raw_response or {})
            claims = list(raw.get("claims") or [])
            if body.claim_index >= len(claims):
                raise HTTPException(status_code=404, detail="Карточка разбора не найдена")
            claim = dict(claims[body.claim_index])
            allowed = {"title", "purpose", "text", "name", "value", "unit", "condition", "page", "quote",
                       "symptom", "cause", "remedy", "node", "steps", "description"}
            if set(body.changes) - allowed:
                raise HTTPException(status_code=422, detail="Это поле нельзя изменить в карточке разбора")
            if body.action == "edit":
                claim.update(body.changes)
                claim["human_verified"] = False
                claim["review_status"] = "edited"
                claim["reviewer"] = body.reviewer.strip() or None
                claim["review_note"] = "Изменено человеком; требуется отдельная проверка"
                valid, errors = _verify_record(str(claim.get("kind") or ""), claim, pages)
                claim["auto_verified"] = bool(valid)
                claim["validation_errors"] = errors
            elif body.action == "reject":
                claim["review_status"] = "rejected"
                claim["human_verified"] = False
                claim["auto_verified"] = False
                claim["reviewer"] = body.reviewer.strip() or None
                claim["review_note"] = body.reason.strip() or "Отклонено проверяющим"
            else:
                kind = str(claim.get("kind") or "")
                valid, errors = _verify_record(kind, claim, pages)
                claim_text = " ".join(str(claim.get(key) or "") for key in (
                    "title", "purpose", "text", "name", "value", "unit", "condition",
                    "symptom", "cause", "remedy", "node", "description",
                )) + " " + " ".join(map(str, claim.get("steps") or []))
                has_numeric_claim = kind in {"technical_norms", "maintenance_norms"} or bool(re.search(r"\d", claim_text))
                if has_numeric_claim and not valid:
                    raise HTTPException(status_code=409, detail="Числовой факт нельзя подтвердить вручную, пока цитата, страница и все числа не проходят программную сверку.")
                if not valid and (not body.reviewer.strip() or not body.reason.strip()):
                    raise HTTPException(status_code=409, detail="Для ручного подтверждения укажите проверяющего и основание; цитата/страница останутся в карточке.")
                if not claim.get("page") or not claim.get("quote"):
                    raise HTTPException(status_code=409, detail="Для подтверждения нужна страница и записанная цитата из PDF.")
                claim["review_status"] = "verified"
                claim["human_verified"] = True
                claim["auto_verified"] = False
                claim["reviewer"] = body.reviewer.strip() or "Проверено человеком"
                claim["review_note"] = body.reason.strip() or ("Цитата и число проверены программно; подтверждено человеком." if valid else "Проверено человеком по изображению/оригиналу PDF.")
                claim["validation_errors"] = [] if valid else errors
            claims[body.claim_index] = claim
            raw["claims"] = claims
            raw["review_updated_at"] = datetime.now().astimezone().isoformat()
            run.raw_response = raw
            session.commit()
            return {"claim": claim, "analysis": analysis_payload(run)}

    @app.post("/api/admin/analysis/{run_id}/publish")
    async def admin_analysis_publish(run_id: int, body: AnalysisPublishRequest):
        counts = {"nodes": 0, "adjustments": 0, "norms": 0, "problems": 0, "notes": 0}
        with Session(app.state.engine) as session:
            run = session.get(AnalysisRun, run_id)
            if run is None or run.source_document_id is None:
                raise HTTPException(status_code=404, detail="Разбор документа не найден")
            machine = session.get(Machine, run.machine_id)
            document = session.get(SourceDocument, run.source_document_id)
            if machine is None or machine.is_demo or machine.status == "archived" or document is None:
                raise HTTPException(status_code=404, detail="Рабочая машина или PDF не найдены")
            raw = dict(run.raw_response or {})
            claims = list(raw.get("claims") or [])
            published = set(raw.get("published_claim_indexes") or [])
            pages = {row.page_number: row.extracted_text for row in session.scalars(select(DocumentPage).where(
                DocumentPage.source_document_id == run.source_document_id
            )).all()}
            nodes_by_title = {node.title.casefold(): node for node in session.scalars(select(Node).where(Node.machine_id == machine.id)).all()}
            next_number = (session.scalar(select(Node.number).where(Node.machine_id == machine.id).order_by(Node.number.desc()).limit(1)) or 0) + 1

            def find_or_create_node(title: str, purpose: str = "", page: int | None = None, quote: str | None = None):
                nonlocal next_number
                name = (title or "Узел из руководства").strip()[:240]
                key = name.casefold()
                if key in nodes_by_title:
                    node = nodes_by_title[key]
                    if purpose and not node.purpose:
                        node.purpose = purpose[:4000]
                    return node
                node = Node(machine_id=machine.id, number=next_number, title=name,
                            purpose=purpose[:4000], source_page=page, source_quote=quote,
                            status="published")
                next_number += 1
                session.add(node)
                session.flush()
                nodes_by_title[key] = node
                counts["nodes"] += 1
                return node

            selected = []
            for index, claim in enumerate(claims):
                if index in published:
                    continue
                state = claim.get("review_status")
                if body.mode == "auto_verified":
                    if state != "auto_verified" or not claim.get("auto_verified") or claim.get("kind") == "gaps":
                        continue
                    valid, errors = _verify_record(str(claim.get("kind") or ""), claim, pages)
                    if not valid:
                        claim["auto_verified"] = False
                        claim["review_status"] = "rejected"
                        claim["validation_errors"] = errors
                        continue
                elif state != "verified" or not claim.get("human_verified"):
                    continue
                selected.append((index, claim))

            known_norms: dict[str, set[str]] = {}
            existing_norms = session.scalars(
                select(Norm).join(Adjustment, Norm.adjustment_id == Adjustment.id)
                .join(Node, Adjustment.node_id == Node.id).where(Node.machine_id == machine.id)
            ).all()
            for existing_norm in existing_norms:
                key = normalize(existing_norm.name) + "\\0" + normalize(existing_norm.condition or "")
                value = normalize(f"{existing_norm.value_original} {existing_norm.unit_original or ''}")
                known_norms.setdefault(key, set()).add(value)
            deduplicated_selected = []
            duplicate_indexes = []
            for index, claim in selected:
                if claim.get("kind") not in {"technical_norms", "maintenance_norms"}:
                    deduplicated_selected.append((index, claim))
                    continue
                key = normalize(str(claim.get("name") or "")) + "\\0" + normalize(str(claim.get("condition") or ""))
                value = normalize(f"{claim.get('value') or ''} {claim.get('unit') or ''}")
                known_values = known_norms.setdefault(key, set())
                if known_values and value not in known_values:
                    raise HTTPException(status_code=409, detail="Обнаружены противоречащие числовые нормы с одинаковым названием и условием. Проверьте/отклоните конфликтующие карточки; публикация остановлена.")
                if value in known_values:
                    duplicate_indexes.append(index)
                    published.add(index)
                    continue
                known_values.add(value)
                deduplicated_selected.append((index, claim))
            selected = deduplicated_selected

            for index, claim in selected:
                kind = str(claim.get("kind") or "")
                page = claim.get("page") if isinstance(claim.get("page"), int) else None
                quote = str(claim.get("quote") or "") or None
                is_human = claim.get("review_status") == "verified"
                if kind == "components":
                    title = str(claim.get("title") or "Узел из руководства")
                    purpose = str(claim.get("purpose") or "")
                    find_or_create_node(title, purpose, page, quote)
                elif kind == "adjustments":
                    node = find_or_create_node(str(claim.get("node") or claim.get("title") or "Узел из руководства"),
                                               page=page, quote=quote)
                    steps = [str(step) for step in claim.get("steps", []) if str(step).strip()]
                    if page:
                        steps.append(f"Источник: локальный PDF, стр. {page}.")
                    session.add(Adjustment(node_id=node.id, title=str(claim.get("title") or "Регулировка")[:240],
                                           instructions=steps, status="published"))
                    counts["adjustments"] += 1
                elif kind in {"technical_norms", "maintenance_norms"}:
                    node = find_or_create_node(str(claim.get("node") or "Параметры из руководства"), page=page, quote=quote)
                    adjustment = Adjustment(node_id=node.id, title="Числовые нормы из руководства",
                                            instructions=[], status="published")
                    session.add(adjustment)
                    session.flush()
                    session.add(Norm(
                        adjustment_id=adjustment.id, name=str(claim.get("name") or "Норма из руководства")[:300],
                        condition=str(claim.get("condition") or ""), value_original=str(claim.get("value") or "")[:240],
                        unit_original=str(claim.get("unit") or "")[:80] or None,
                        source_kind="document", source_document_id=document.id,
                        source_page_start=page, source_page_end=page, source_quote=quote,
                        review_status="verified" if is_human else "auto_verified",
                    ))
                    counts["norms"] += 1
                elif kind == "faults":
                    symptom = str(claim.get("symptom") or "Неисправность из руководства")
                    problem = Problem(machine_id=machine.id, title=symptom[:320],
                                      symptom=f"Симптом: {symptom}\\nПричина: {claim.get('cause') or 'не указана'}",
                                      source_page=page, source_quote=quote, status="published")
                    problem.steps.append(ProblemStep(step_number=1, instruction=str(claim.get("remedy") or "См. руководство"),
                                                     source_page=page, source_quote=quote))
                    session.add(problem)
                    counts["problems"] += 1
                elif kind in {"maintenance", "safety", "gaps"}:
                    title = {"maintenance": "Обслуживание", "safety": "Безопасность", "gaps": "Пробел документа"}[kind]
                    text = str(claim.get("text") or claim.get("description") or "")
                    machine.characteristics.append(MachineCharacteristic(
                        label=f"{title}: {claim.get('title') or claim.get('name') or kind}",
                        display_value=text[:240], source_page=page, source_quote=quote,
                        auto_verified=bool(claim.get("auto_verified")) and not is_human,
                        human_verification_required=(kind == "gaps"), status="published",
                    ))
                    counts["notes"] += 1
                published.add(index)
            session.flush()
            machine.status = "published"
            machine.version += 1
            raw["published_claim_indexes"] = sorted(published)
            if duplicate_indexes:
                raw.setdefault("deduplicated_claim_indexes", []).extend(
                    index for index in duplicate_indexes if index not in raw.get("deduplicated_claim_indexes", [])
                )
            raw["last_publication"] = {"mode": body.mode, "reviewer": body.reviewer.strip() or None,
                                       "at": datetime.now().astimezone().isoformat(), "counts": counts}
            run.raw_response = raw
            session.commit()
            return {"published": sum(counts.values()), "deduplicated_claims": len(duplicate_indexes), "counts": counts,
                    "machine_id": machine.id, "mode": body.mode,
                    "label": "Проверено человеком" if body.mode == "human_verified" else "Автопроверено; человеком не сверено"}

    @app.get("/api/admin/assistant/status")
    async def admin_assistant_status():
        with Session(app.state.engine) as session:
            return ollama_status(session)

    @app.post("/api/admin/assistant/answer")
    async def admin_assistant_answer(body: AssistantRequest):
        with Session(app.state.engine) as session:
            machine = session.get(Machine, body.machine_id)
            if machine is None or machine.is_demo or machine.status == "archived":
                raise HTTPException(status_code=404, detail="Рабочая техника не найдена")
            if is_diagnostic_question(body.question):
                manuals = session.scalars(select(SourceDocument).where(
                    SourceDocument.machine_id == body.machine_id
                ).order_by(SourceDocument.uploaded_at.desc())).all()
                manual = next((candidate for candidate in manuals if exact_manual_for_machine(candidate)), None)
                if manual is None:
                    return {"status": "diagnostic_manual_missing",
                            "answer": "Для диагностики по таблице неисправностей нужен точный PDF руководства Kolibri ГРН-471 v4. Загрузите его в локальную базу этой машины. Другие документы не будут подменены этим источником.",
                            "citations": [], "model_used": False}
                case = match_manual_diagnosis(body.question)
                page_numbers = ({30, 13, *(item["page"] for item in case.get("manual_sources", []))}
                                if case is not None else {30})
                source_pages = {page.page_number: page for page in session.scalars(select(DocumentPage).where(
                    DocumentPage.source_document_id == manual.id,
                    DocumentPage.page_number.in_(page_numbers),
                )).all()}
                result = diagnostic_answer(case, question=body.question.strip(), document_id=manual.id,
                                           document_title=manual.title, filename=manual.original_filename)
                # Verify every displayed quotation against the local extracted PDF page.
                verified_citations = []
                for citation in result["citations"]:
                    page = source_pages.get(citation["page"])
                    source_text = re.sub(r"\s+", "", page.extracted_text or "").casefold() if page else ""
                    cited_text = re.sub(r"\s+", "", citation["quote"]).casefold()
                    if not source_text or cited_text not in source_text:
                        return {"status": "diagnostic_source_check_failed",
                                "answer": "Не удалось сверить текст диагностики с нужной страницей PDF. Причина не сформирована; проверьте распознавание страницы вручную.",
                                "citations": [], "model_used": False}
                    citation["viewer_url"] = f"/api/admin/documents/{manual.id}/file#page={citation['page']}"
                    verified_citations.append(citation)
                result["citations"] = verified_citations
                return result
            hits = search_pages(session, body.question, machine_id=body.machine_id, limit=6)
            part_answer = None
            if is_parts_query(body.question):
                part_hits = search_part_graph(session, body.question, machine_id=body.machine_id, limit=2)
                if part_hits:
                    part_answer = _part_lookup_response(part_hits)
                    if part_answer is not None:
                        part_answer["evidence"] = [hit.public_dict() for hit in part_hits]
            settings = model_settings(session)
            local_status = ollama_status(session)
            local_model_ready = bool(local_status["running"] and local_status["chat_model_ready"])
            if part_answer is None and local_model_ready:
                semantic_hits = semantic_manual_hits(session, body.machine_id, body.question.strip(), settings["search_model"])
                by_page = {(hit.document_id, hit.page): hit for hit in hits}
                for hit in semantic_hits:
                    existing = by_page.get((hit.document_id, hit.page))
                    if existing:
                        existing.score += hit.score
                    else:
                        by_page[(hit.document_id, hit.page)] = hit
                hits = sorted(by_page.values(), key=lambda hit: hit.score, reverse=True)[:8]
        if part_answer is not None:
            result = part_answer
        elif not local_model_ready:
            result = {
                "status": "not_configured",
                "answer": "Локальная Ollama-модель не готова. Установите Ollama и загрузите выбранную модель; облачный fallback отключён. Найденные выдержки доступны для ручной проверки.",
                "citations": [], "model_used": False, "local_only": True,
                "evidence": [hit.public_dict() for hit in hits],
            }
        else:
            result = generate_local_answer(body.question.strip(), hits, settings["chat_model"])
            result["evidence"] = [hit.public_dict() for hit in hits]
        for citation in result.get("citations", []):
            citation["viewer_url"] = f"/api/admin/documents/{citation['document_id']}/file#page={citation['page']}"
        return result

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
    # Мобильный интерфейс существует только внутри APK; ПК-сервер отдаёт только админку/API.
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
