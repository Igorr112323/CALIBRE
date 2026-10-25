from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy.orm import Session

from calibre.database import initialize_database, make_engine
from calibre.knowledge import search_pages, validate_document_scope
from calibre.models import AppSetting, DocumentPage, Machine, SourceDocument


class ManualRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database = Path(self.temp_dir.name) / "knowledge.sqlite3"
        self.engine = make_engine(f"sqlite:///{database.as_posix()}")
        self.sessions = initialize_database(self.engine, include_demo=False)
        with Session(self.engine) as session:
            machine = Machine(code="GRN-471", manufacturer="Kolibri", model="ГРН-471",
                              machine_type="Грабли", status="draft", is_demo=False)
            document = SourceDocument(machine=machine, title="Руководство", original_filename="manual.pdf",
                                      sha256="a" * 64, page_count=4, storage_path="documents/manual.pdf")
            session.add(document)
            session.flush()
            pages = [
                "Описание ротора и число вращений. Частота вращения ротора 66 об/мин.",
                "Для настройки плоскости штанг разница расстояния слева и справа должна быть 10 мм. "
                "Абсолютный зазор до земли и число оборотов регулировочного винта не указаны.",
                "При транспортировании на уклоне не более 20 градусов скорость снизить до 3…6 км/ч.",
                "Каталог деталей. Подшипник опорной каретки, позиция 14, номер 12345.",
            ]
            for number, text in enumerate(pages, 1):
                session.add(DocumentPage(source_document_id=document.id, page_number=number,
                                         extracted_text=text, extraction_method="text", text_quality="good"))
            session.add(AppSetting(key=f"document_scope:{document.id}", value=(
                '{"guide_start_page":1,"guide_end_page":3,"catalog_start_page":4,"catalog_end_page":4}'
            )))
            session.commit()
            self.machine_id = machine.id
            self.document_id = document.id

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_russian_inflections_find_the_adjustment_page(self):
        with Session(self.engine) as session:
            hits = search_pages(session, "Какая разница между зубьями слева и справа при настройке?",
                                machine_id=self.machine_id)
        self.assertTrue(hits)
        self.assertEqual(hits[0].page, 2)
        self.assertIn("10 мм", hits[0].text)
        self.assertEqual(hits[0].section, "manual")

    def test_parts_catalog_is_excluded_from_norm_and_adjustment_queries(self):
        with Session(self.engine) as session:
            hits = search_pages(session, "настройки винта", machine_id=self.machine_id)
        self.assertTrue(hits)
        self.assertNotIn(4, [hit.page for hit in hits])

    def test_parts_lookup_can_search_catalog_but_clearly_labels_it(self):
        with Session(self.engine) as session:
            hits = search_pages(session, "найди каталожный номер подшипника каретки", machine_id=self.machine_id)
        self.assertTrue(hits)
        self.assertEqual(hits[0].page, 4)
        self.assertEqual(hits[0].section, "parts_catalog")

    def test_unsupported_question_has_no_fabricated_retrieval_result(self):
        with Session(self.engine) as session:
            hits = search_pages(session, "какой цвет кабины у трактора?", machine_id=self.machine_id)
        self.assertEqual(hits, [])

    def test_catalog_query_detection_yields_to_regulation_scope(self):
        with Session(self.engine) as session:
            hits = search_pages(session, "регулировка номера детали подшипника", machine_id=self.machine_id)
        self.assertNotIn(4, [hit.page for hit in hits])

    def test_page_scope_rejects_overlap_and_out_of_range(self):
        with self.assertRaises(ValueError):
            validate_document_scope(56, 4, 35, 34, 56)
        with self.assertRaises(ValueError):
            validate_document_scope(56, 4, 33, 57, 60)
        self.assertEqual(validate_document_scope(56, 4, 33, 34, 56), {
            "guide_start_page": 4, "guide_end_page": 33,
            "catalog_start_page": 34, "catalog_end_page": 56,
        })


if __name__ == "__main__":
    unittest.main()
