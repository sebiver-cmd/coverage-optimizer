#!/usr/bin/env python
"""CLI entrypoint for data retention pruning (Task 10.2).

Usage::

    python scripts/run_retention.py          # uses DATABASE_URL from env / .env
    python -m scripts.run_retention          # alternative invocation

Exits with code 0 on success, 1 on error.
"""

from __future__ import annotations

import sys

from backend.config import get_settings
from backend.db import init_engine, get_db
from backend.retention import run_retention


def main() -> int:
    settings = get_settings()

    if not settings.retention_enabled:
        print("Retention is disabled (RETENTION_ENABLED=false). Nothing to do.")
        return 0

    engine = init_engine()
    if engine is None:
        print("ERROR: DATABASE_URL is not configured — cannot run retention.", file=sys.stderr)
        return 1

    # Obtain a session from the generator
    db_gen = get_db()
    db = next(db_gen)
    try:
        result = run_retention(db, settings)
        print("Retention completed successfully.")
        print(f"  Cutoffs:  {result['cutoffs']}")
        print(f"  Pruned:   {result['pruned_counts']}")
        return 0
    except Exception as exc:
        print(f"ERROR: Retention failed: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            next(db_gen, None)
        except StopIteration:
            pass


if __name__ == "__main__":
    sys.exit(main())
