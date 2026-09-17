"""MDM database configuration isolated from the legacy SQLite application."""

import os
import threading
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


MDM_DATABASE_URL_ENV = "MDM_DATABASE_URL"
POOL_SIZE = 5
MAX_OVERFLOW = 10
POOL_TIMEOUT_SECONDS = 30

_resource_lock = threading.RLock()
_shared_database_url: Optional[str] = None
_shared_engine: Optional[Engine] = None
_shared_session_factory = None


def get_database_url():
    database_url = os.environ.get(MDM_DATABASE_URL_ENV, "").strip()
    if not database_url:
        raise RuntimeError(
            "MDM_DATABASE_URL is required, for example "
            "mysql+pymysql://user:password@host:3306/database?charset=utf8mb4"
        )
    if not database_url.startswith("mysql+pymysql://"):
        raise RuntimeError("MDM_DATABASE_URL must use the mysql+pymysql driver")
    return database_url


def get_engine():
    global _shared_database_url, _shared_engine

    database_url = get_database_url()
    with _resource_lock:
        if _shared_engine is None:
            _shared_engine = create_engine(
                database_url,
                pool_size=POOL_SIZE,
                max_overflow=MAX_OVERFLOW,
                pool_timeout=POOL_TIMEOUT_SECONDS,
                pool_pre_ping=True,
                future=True,
            )
            _shared_database_url = database_url
        elif _shared_database_url != database_url:
            raise RuntimeError(
                "MDM_DATABASE_URL cannot change after shared Engine initialization"
            )
        return _shared_engine


def get_session_factory():
    global _shared_session_factory

    with _resource_lock:
        if _shared_session_factory is None:
            _shared_session_factory = sessionmaker(
                bind=get_engine(), autoflush=False, expire_on_commit=False
            )
        return _shared_session_factory


def dispose_shared_engine():
    """Dispose process-level MDM database resources during application shutdown."""
    global _shared_database_url, _shared_engine, _shared_session_factory

    with _resource_lock:
        engine = _shared_engine
        _shared_database_url = None
        _shared_engine = None
        _shared_session_factory = None
    if engine is not None:
        engine.dispose()
