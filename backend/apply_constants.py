"""Shared constants for the apply-prices endpoints.

Both :mod:`backend.apply_prices_api` (dry-run) and
:mod:`backend.apply_real_api` (real apply) use the same batch
directory, UUID validation pattern, and audit log path.
Centralising them here avoids duplication.
"""

from __future__ import annotations

import re
from pathlib import Path

# Directory where batch manifests are persisted / read.
BATCH_DIR = Path("data/apply_batches")

# Audit log file path (JSONL).
AUDIT_LOG = Path("data/apply_audit.log")

# Strict UUID-4 pattern (lowercase hex with hyphens).
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
