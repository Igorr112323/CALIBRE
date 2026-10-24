import unittest

from sqlalchemy.exc import IntegrityError

from calibre.database import make_engine
from calibre.models import Adjustment, Base, Machine, Node, Norm, Photo


class DatabaseConstraintTests(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine("sqlite://")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_norm_cannot_be_saved_without_a_document_or_department_measurement_source(self):
        from sqlalchemy.orm import Session
        with Session(self.engine) as session:
            machine = Machine(code="M-1", manufacturer="Тест", model="Тест", machine_type="Тест")
            node = Node(machine=machine, number=1, title="Узел")
            adjustment = Adjustment(node=node, title="Регулировка")
            session.add(Norm(adjustment=adjustment, value_original="250", source_kind="document"))
            with self.assertRaises(IntegrityError):
                session.commit()

    def test_internal_photo_cannot_be_marked_published(self):
        from sqlalchemy.orm import Session
        with Session(self.engine) as session:
            machine = Machine(code="M-2", manufacturer="Тест", model="Тест", machine_type="Тест")
            session.add(Photo(machine=machine, storage_path="photos/private.jpg", usage_rights="internal_only", published=True))
            with self.assertRaises(IntegrityError):
                session.commit()


if __name__ == "__main__":
    unittest.main()
