"""Database engine, session factory, and FastAPI dependency for SB-Optima.

Usage
-----
- If ``DATABASE_URL`` is **not** set (or empty), all DB helpers are inert:
  ``get_db()`` will raise an error only if actually called, and no engine is
  created at import time.
- If ``DATABASE_URL`` **is** set, call :func:`init_engine` once (e.g. in a
  FastAPI startup event) to initialise the engine and session factory.

This module intentionally avoids import-time side effects so that the
existing test suite can run without a database.
"""

from __future__ import annotations

import os
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

# ---------------------------------------------------------------------------
# Declarative base — shared by all future models
# ---------------------------------------------------------------------------

Base = declarative_base()

# ---------------------------------------------------------------------------
# Module-level state (initialised lazily via ``init_engine``)
# ---------------------------------------------------------------------------

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def init_engine(database_url: str | None = None) -> Engine | None:
    """Create (or re-create) the module-level engine and session factory.

    Parameters
    ----------
    database_url:
        A SQLAlchemy database URL.  If *None* or empty, the function reads
        ``DATABASE_URL`` from the environment.  If still empty, the function
        returns *None* and DB support stays disabled.

    Returns
    -------
    The :class:`~sqlalchemy.engine.Engine` instance, or *None* when no URL
    is available.
    """
    global _engine, _SessionLocal  # noqa: PLW0603

    url = database_url or os.environ.get("DATABASE_URL", "")
    if not url:
        return None

    _engine = create_engine(url, pool_pre_ping=True)
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return _engine


def get_engine() -> Engine | None:
    """Return the current engine (may be *None* if not initialised)."""
    return _engine


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy session.

    Typical usage in a route::

        @router.get("/example")
        def example(db: Session = Depends(get_db)):
            ...

    The session is automatically closed after the request.
    """
    if _SessionLocal is None:
        raise RuntimeError(
            "Database is not configured. Set DATABASE_URL and call init_engine()."
        )
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db() -> str:
    """Cheap connectivity probe (``SELECT 1``).

    Returns
    -------
    ``"ok"`` on success, ``"error"`` on failure, or
    ``"skipped"`` when no engine has been initialised.
    """
    if _engine is None:
        return "skipped"
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001
        return "error"
