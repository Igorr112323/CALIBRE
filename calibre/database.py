"""SQLite setup and safely separated, unmistakably-labelled demonstration records."""
from __future__ import annotations

from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from calibre.demo import seed_demo_data

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "calibre.sqlite3"


def make_engine(database_url: str | None = None) -> Engine:
    if database_url is None:
        DEFAULT_DATABASE.parent.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{DEFAULT_DATABASE.as_posix()}"
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def initialize_database(engine: Engine, include_demo: bool = True) -> sessionmaker[Session]:
    config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    database_url = engine.url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "head")
    sessions = make_session_factory(engine)
    if include_demo:
        with sessions() as session:
            seed_demo_data(session)
    return sessions
