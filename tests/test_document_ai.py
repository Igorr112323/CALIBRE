from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import Session

from calibre.document_ai import TASKS, _worker
from calibre.models import AnalysisRun, DocumentPage, Machine, SourceDocument
from server import create_app


class PersistentDocumentWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app = create_app(
            f"sqlite:///{(Path(self.temp_dir.name) / 'worker.sqlite3').as_posix()}",
            include_demo=False,
            storage_root=Path(self.temp_dir.name) / "store",
        )
        with Session(self.app.state.engine) as session:
            machine = Machine(code="TEST-1", manufacturer="Test", model="Test machine", machine_type="Test")
            session.add(machine)
            session.flush()
            document = SourceDocument(
                machine_id=machine.id, title="Fixture manual", original_filename="fixture.pdf",
                sha256="a" * 64, page_count=1, storage_path="documents/1/fixture.pdf", language="ru",
            )
            session.add(document)
            session.flush()
            session.add(DocumentPage(
                source_document_id=document.id, page_number=1,
                extracted_text="Инструкция: проверочный текст без числовых норм.",
                extraction_method="text", text_quality="good",
            ))
            run = AnalysisRun(
                machine_id=machine.id, source_document_id=document.id,
                prompt_version="test", stage="queued", progress_percent=0, status="queued",
                raw_response={"sections": {}, "claims": [], "rejected": []},
            )
            session.add(run)
            session.commit()
            self.run_id = run.id

    def tearDown(self):
        self.app.state.engine.dispose()

    @patch("calibre.document_ai.embeddings", side_effect=RuntimeError("not installed"))
    @patch("calibre.document_ai.chat_json", return_value={})
    @patch("calibre.document_ai.ollama_status", return_value={
        "running": True, "chat_model_ready": True, "search_model_ready": True,
    })
    def test_worker_persists_all_sections_and_finishes_in_review_without_claims(self, status, chat, embed):
        _worker(self.app, self.run_id)
        with Session(self.app.state.engine) as session:
            run = session.get(AnalysisRun, self.run_id)
            self.assertEqual(run.status, "review")
            self.assertEqual(run.progress_percent, 100)
            self.assertEqual(set(run.raw_response["sections"]), {task[0] for task in TASKS})
            self.assertEqual(run.raw_response["claims"], [])
            self.assertIn("embedding", run.raw_response["search_index"])
        self.assertEqual(chat.call_count, len(TASKS))
        status.assert_called_once()
        embed.assert_called_once()

    @patch("calibre.document_ai.chat_json")
    @patch("calibre.document_ai.ollama_status", return_value={
        "running": False, "chat_model_ready": False, "search_model_ready": False,
    })
    def test_worker_waits_for_local_model_without_fabricating_result(self, status, chat):
        _worker(self.app, self.run_id)
        with Session(self.app.state.engine) as session:
            run = session.get(AnalysisRun, self.run_id)
            self.assertEqual(run.status, "waiting_for_model")
            self.assertEqual(run.progress_percent, 0)
            self.assertEqual(run.raw_response["claims"], [])
        status.assert_called_once()
        chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
