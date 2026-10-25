"""Локальный сервер КАЛИБР: published-каталог и узкие телефонные очереди."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import uuid
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from calibre.assistant import generate_grounded_answer, model_status
from calibre.diagnostics import (diagnostic_answer, exact_manual_for_machine,
                                 is_diagnostic_question, match_manual_diagnosis)
from calibre.catalog import mobile_catalog
from calibre.database import DATA_ROOT, initialize_database, make_engine
from calibre.knowledge import is_parts_query, search_pages, search_part_graph, validate_document_scope
from calibre.part_graph import CATALOG_SHA256, build_part_graph
from calibre.models import (Adjustment, AppSetting, DocumentPage, ExpertQuestion, Feedback, Machine,
                            Node, Norm, Photo, Problem, SourceDocument)

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
PREVIEW_ROOT = ROOT / "mobile" / "www"
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


class NodeCreate(BaseModel):
    machine_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=240)
    purpose: str = Field(default="", max_length=4000)


class AssistantRequest(BaseModel):
    machine_id: int = Field(gt=0)
    question: str = Field(min_length=1, max_length=2000)


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


def create_app(database_url: str | None = None, include_demo: bool = True, enable_preview: bool = True,
               storage_root: str | Path | None = None) -> FastAPI:
    engine = make_engine(database_url)
    sessions = initialize_database(engine, include_demo=include_demo)
    app = FastAPI(title="КАЛИБР", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine
    app.state.session_factory = sessions
    app.state.data_root = Path(storage_root).resolve() if storage_root is not None else DATA_ROOT.resolve()

    @app.middleware("http")
    async def local_admin_and_write_guard(request, call_next):
        path = request.url.path
        remote = not is_loopback(request.client.host if request.client else None)
        if remote and (
            path == "/admin" or path.startswith("/admin/")
            or path == "/preview" or path.startswith("/preview/")
            or path == "/api/admin" or path.startswith("/api/admin/")
        ):
            return JSONResponse(status_code=403, content={
                "ok": False,
                "message": "Админка, документы и ИИ-предпросмотр доступны только на компьютере-сервере",
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

    @app.get("/api/admin/stats")
    async def admin_stats():
        with Session(app.state.engine) as session:
            real_machines = session.scalars(select(Machine).where(Machine.is_demo.is_(False))).all()
            document_count = len(session.scalars(select(SourceDocument.id)).all())
            page_count = len(session.scalars(select(DocumentPage.id)).all())
            photo_count = len(session.scalars(select(Photo.id)).all())
            questions = len(session.scalars(select(ExpertQuestion.id).where(ExpertQuestion.status == "new")).all())
            pending = len(session.scalars(select(Node.id).where(Node.status.in_(("ai_draft", "review")))).all())
        return {"machines": len(real_machines), "documents": document_count, "pages": page_count,
                "images": photo_count, "new_questions": questions, "draft_nodes": pending,
                "ai": model_status()}

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
            session.commit()
            return {"id": machine.id, "code": machine.code, "manufacturer": machine.manufacturer,
                    "model": machine.model, "machine_type": machine.machine_type,
                    "description": machine.description, "status": machine.status}

    @app.get("/api/admin/nodes")
    async def admin_nodes(machine_id: int = Query(gt=0)):
        with Session(app.state.engine) as session:
            machine = session.get(Machine, machine_id)
            if machine is None or machine.is_demo:
                raise HTTPException(status_code=404, detail="Рабочая техника не найдена")
            nodes = session.scalars(select(Node).where(Node.machine_id == machine_id).order_by(Node.number)).all()
            return [{"id": node.id, "number": node.number, "title": node.title,
                     "purpose": node.purpose, "status": node.status} for node in nodes]

    @app.post("/api/admin/nodes", status_code=201)
    async def admin_create_node(body: NodeCreate):
        with Session(app.state.engine) as session:
            machine = session.get(Machine, body.machine_id)
            if machine is None or machine.is_demo or machine.status == "archived":
                raise HTTPException(status_code=404, detail="Рабочая техника не найдена")
            max_number = session.scalar(select(Node.number).where(Node.machine_id == body.machine_id)
                                        .order_by(Node.number.desc()).limit(1)) or 0
            node = Node(machine_id=body.machine_id, number=max_number + 1,
                        title=body.title.strip(), purpose=body.purpose.strip(), status="review")
            session.add(node)
            session.commit()
            return {"id": node.id, "number": node.number, "title": node.title,
                    "purpose": node.purpose, "status": node.status}

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
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    destination.unlink(missing_ok=True)
                    raise HTTPException(status_code=409, detail="PDF не удалось сохранить: повторная загрузка")
                return {"id": document.id, "machine_id": machine_id, "title": document.title,
                        "filename": safe_name, "page_count": page_count,
                        "pages_with_text": sum(1 for page in session.scalars(select(DocumentPage).where(
                            DocumentPage.source_document_id == document.id)).all() if page.text_quality == "good"),
                        "scope": scope, "sha256": sha256,
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
                     "filename": Path(photo.storage_path).name,
                     "url": f"/api/admin/media/{photo.id}/file"} for photo in photos]

    @app.post("/api/admin/media", status_code=201)
    async def admin_upload_media(
        machine_id: int = Form(gt=0),
        file: UploadFile = File(...),
        caption: str = Form(default="", max_length=300),
        node_id: int | None = Form(default=None, gt=0),
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
                duplicate = session.scalar(select(Photo).where(Photo.machine_id == machine_id,
                                                               Photo.storage_path == relative.as_posix()))
                if duplicate:
                    raise HTTPException(status_code=409, detail="Это изображение уже загружено")
                staged.replace(destination)
                photo = Photo(machine_id=machine_id, node_id=node_id, storage_path=relative.as_posix(),
                              caption=caption.strip(), photo_kind="node" if node_id else "candidate",
                              usage_rights="internal_only", allow_training=False, published=False)
                session.add(photo)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    destination.unlink(missing_ok=True)
                    raise HTTPException(status_code=409, detail="Изображение не удалось сохранить")
                return {"id": photo.id, "machine_id": machine_id, "node_id": photo.node_id,
                        "caption": photo.caption, "filename": destination.name, "bytes": size,
                        "url": f"/api/admin/media/{photo.id}/file"}
        except HTTPException:
            staged.unlink(missing_ok=True)
            raise
        except Exception as exc:
            staged.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="Не удалось загрузить изображение") from exc

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

    @app.get("/api/admin/assistant/status")
    async def admin_assistant_status():
        return model_status()

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
            if is_parts_query(body.question):
                part_hits = search_part_graph(session, body.question, machine_id=body.machine_id, limit=2)
                if part_hits:
                    graph_pages = {(hit.document_id, hit.page) for hit in part_hits}
                    hits = part_hits + [hit for hit in hits if (hit.document_id, hit.page) not in graph_pages]
                    hits = hits[:10]
        result = await generate_grounded_answer(body.question.strip(), hits)
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
    # Собранный в EXE предпросмотр доступен только с loopback (см. middleware выше).
    if enable_preview and PREVIEW_ROOT.is_dir():
        app.mount("/preview", StaticFiles(directory=str(PREVIEW_ROOT), html=True), name="desktop-preview")
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
