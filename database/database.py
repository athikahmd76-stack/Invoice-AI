"""SQLite / SQLAlchemy engine and session management."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from config import AppConfig
from database.models import Base


def _fix_sqlite_types(dbapi_connection, connection_record):
    pass


def create_db_engine(config: AppConfig):
    url = config.database_url
    engine_kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **engine_kwargs)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


class Database:
    """Simple wrapper around engine + session factory."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.engine = create_db_engine(config)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    def init_db(self) -> None:
        Base.metadata.create_all(self.engine)

    @property
    def db_path(self) -> Path:
        url = self.config.database_url
        if url.startswith("sqlite:///"):
            return Path(url.split("sqlite:///", 1)[1])
        return self.config.data_dir / "invoiceai.db"

    @property
    def db_size_mb(self) -> float:
        path = self.db_path
        if path.exists():
            return path.stat().st_size / (1024 * 1024)
        return 0.0

    def session(self) -> Session:
        return self.SessionLocal()

    def now_iso(self) -> str:
        return dt.datetime.now().isoformat(timespec="seconds")


def json_dumps(data: Any) -> str | None:
    if data is None:
        return None
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def json_loads(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None