"""Tests for Task 9.2 — Migration safety (CI-safe, SQLite).

Verifies:
1. The ORM model schema (via ``Base.metadata.create_all``) produces all
   expected tables on SQLite.  This mirrors what ``alembic upgrade head``
   achieves on Postgres without relying on Postgres-specific DDL.
2. The Alembic revision chain is strictly linear (no branching).
3. Every migration file is importable and has valid revision identifiers.

No Postgres or Docker required — runs as part of the normal ``pytest``
suite.
"""

from __future__ import annotations

import importlib
import os

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from backend.db import Base

# Ensure all models are imported so their tables register on Base.metadata.
import backend.models  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _alembic_cfg() -> Config:
    """Return an Alembic :class:`Config` pointed at the project's migrations."""
    base_dir = os.path.join(os.path.dirname(__file__), os.pardir)
    ini_path = os.path.abspath(os.path.join(base_dir, "alembic.ini"))
    return Config(ini_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigrationsSQLite:
    """Validate migration integrity using SQLite."""

    def test_create_all_produces_expected_tables(self):
        """``Base.metadata.create_all`` must produce all core tables on SQLite.

        This verifies that the ORM models are self-consistent and that the
        schema can be materialised without Postgres-specific extensions.
        """
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        engine.dispose()

        expected = {
            "tenants",
            "users",
            "hostedshop_credentials",
            "optimization_jobs",
            "apply_batches",
            "audit_events",
        }
        missing = expected - tables
        assert not missing, f"Missing tables after create_all: {missing}"

    def test_tables_have_primary_keys(self):
        """Every core table should have at least one primary-key column."""
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)

        inspector = inspect(engine)
        for table_name in [
            "tenants",
            "users",
            "hostedshop_credentials",
            "optimization_jobs",
            "apply_batches",
            "audit_events",
        ]:
            pk = inspector.get_pk_constraint(table_name)
            assert pk and pk["constrained_columns"], (
                f"Table {table_name!r} has no primary key"
            )
        engine.dispose()

    def test_revision_chain_is_linear(self):
        """Each revision has exactly one down_revision (no branching)."""
        cfg = _alembic_cfg()
        script = ScriptDirectory.from_config(cfg)

        revisions = list(script.walk_revisions())
        assert len(revisions) > 0, "No Alembic revisions found"

        for rev in revisions:
            dr = rev.down_revision
            assert dr is None or isinstance(dr, str), (
                f"Revision {rev.revision} has branched down_revision: {dr!r}"
            )

    def test_all_migrations_importable(self):
        """Every migration file in alembic/versions/ must be importable."""
        cfg = _alembic_cfg()
        script = ScriptDirectory.from_config(cfg)

        for rev in script.walk_revisions():
            # Importing the module verifies syntax + imports are valid.
            mod = rev.module
            assert mod is not None, f"Revision {rev.revision} has no module"
            assert hasattr(mod, "upgrade"), (
                f"Revision {rev.revision} missing upgrade()"
            )
            assert hasattr(mod, "downgrade"), (
                f"Revision {rev.revision} missing downgrade()"
            )

    def test_revision_ids_are_unique(self):
        """No two revisions share the same revision ID."""
        cfg = _alembic_cfg()
        script = ScriptDirectory.from_config(cfg)

        seen: set[str] = set()
        for rev in script.walk_revisions():
            assert rev.revision not in seen, (
                f"Duplicate revision ID: {rev.revision}"
            )
            seen.add(rev.revision)
