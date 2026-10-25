#!/usr/bin/env python3
"""Run retrieval stress cases against the private local GRN-471 v4 PDF.

This tests evidence retrieval and scope separation only. It does not claim to
score a live language model; model-generation tests use a mock provider in
unittest, and a real provider must be configured separately.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from calibre.database import initialize_database, make_engine  # noqa: E402
from calibre.knowledge import search_pages  # noqa: E402
from calibre.models import AppSetting, DocumentPage, Machine, SourceDocument  # noqa: E402

PDF = ROOT / "tests/test_documents/GRN-471-Kolibri-v4.pdf"
CASES = [
    {"id": "adjustment-left-right", "question": "Какую разницу выставить между концами граблин слева и справа?", "expected_pages": [23], "catalog_allowed": False},
    {"id": "unknown-ground-clearance", "question": "Назови точный зазор граблин до земли и количество оборотов винта.", "expected_pages": [10, 23], "catalog_allowed": False},
    {"id": "missing-tightening-torque", "question": "Какой момент затяжки болтов ротора в Н·м?", "expected_pages": [25], "catalog_allowed": False},
    {"id": "guard-removal-safety", "question": "Можно ли запускать машину без защитных ограждений?", "expected_pages": [12], "catalog_allowed": False},
    {"id": "repair-with-engine-running", "question": "Можно ли устранять поломку при работающем двигателе трактора?", "expected_pages": [13], "catalog_allowed": False},
    {"id": "tines-hit-ground", "question": "Граблины задевают землю: что проверить и как исправить?", "expected_pages": [10, 30], "catalog_allowed": False},
    {"id": "transport-slope", "question": "При движении по уклону какой максимальный угол и скорость?", "expected_pages": [13], "catalog_allowed": False},
    {"id": "reducer-lubrication", "question": "Через сколько моточасов смазывать редуктор, сколько точек смазки?", "expected_pages": [26, 27], "catalog_allowed": False},
    {"id": "metallic-knock", "question": "Появился резкий металлический стук редуктора: вероятная причина и устранение?", "expected_pages": [30], "catalog_allowed": False},
    {"id": "reducer-overheating", "question": "Редуктор чрезмерно нагревается, какую неисправность указало руководство?", "expected_pages": [30], "catalog_allowed": False},
    {"id": "parts-catalog-lookup", "question": "Найди каталожный номер подшипника каретки.", "expected_pages": [43, 49], "catalog_allowed": True},
    {"id": "catalog-cannot-prove-adjustment", "question": "Регулировка детали каретки: дай норматив и позицию каталога.", "expected_pages": [10], "catalog_allowed": False},
]


def run() -> dict:
    if not PDF.is_file():
        raise SystemExit(f"Частный PDF не найден: {PDF}\nСкопируйте РуководствоПоГраблям.pdf в эту Git-ignored папку.")
    reader = PdfReader(str(PDF))
    with tempfile.TemporaryDirectory(prefix="calibre-grn-stress-") as folder:
        database = Path(folder) / "stress.sqlite3"
        engine = make_engine(f"sqlite:///{database.as_posix()}")
        initialize_database(engine, include_demo=False)
        with Session(engine) as session:
            machine = Machine(code="GRN-471-STRESS", manufacturer="Kolibri", model="ГРН-471",
                              machine_type="Роторные грабли", status="draft", is_demo=False)
            document = SourceDocument(machine=machine, title="Руководство Kolibri ГРН-471, версия 4",
                                      original_filename=PDF.name,
                                      sha256="local-stress-only", page_count=len(reader.pages),
                                      storage_path="private/manual.pdf", language="ru")
            session.add(document)
            session.flush()
            for page_number, page in enumerate(reader.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                quality = "good" if len(text.strip()) >= 40 else ("low" if text.strip() else "empty")
                session.add(DocumentPage(source_document_id=document.id, page_number=page_number,
                                         extracted_text=text, extraction_method="text" if text else "none",
                                         text_quality=quality))
            scope = {"guide_start_page": 4, "guide_end_page": 33,
                     "catalog_start_page": 34, "catalog_end_page": len(reader.pages)}
            session.add(AppSetting(key=f"document_scope:{document.id}",
                                   value=json.dumps(scope, ensure_ascii=False)))
            session.commit()
            report_cases = []
            for case in CASES:
                hits = search_pages(session, case["question"], machine_id=machine.id, limit=12)
                pages = [hit.page for hit in hits]
                missing = [page for page in case["expected_pages"] if page not in pages]
                leaked_catalog = not case["catalog_allowed"] and any(hit.section == "parts_catalog" for hit in hits)
                report_cases.append({**case, "retrieved_pages": pages,
                                     "missing_expected_pages": missing,
                                     "catalog_leak": leaked_catalog,
                                     "pass": not missing and not leaked_catalog})
        engine.dispose()
    return {"benchmark": "GRN-471 Kolibri v4 local evidence retrieval stress test",
            "source": {"filename": PDF.name, "pages": len(reader.pages), "catalog_pdf_pages": [34, len(reader.pages)]},
            "scope_note": "Каталог деталей используется только для поиска деталей; он исключён из настроек и нормативов.",
            "model_scored": False,
            "cases_total": len(report_cases), "cases_passed": sum(item["pass"] for item in report_cases),
            "cases_failed": [item for item in report_cases if not item["pass"]],
            "cases": report_cases}


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["cases_passed"] == result["cases_total"] else 1)
