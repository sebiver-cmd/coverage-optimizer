"""Tests for SaaS Task 1.2 — Backend becomes sole product fetcher for UI.

Scans all Python files under ``ui/`` and asserts that no direct SOAP usage
strings are present.  This prevents regressions where someone re-introduces
direct ``dandomain_api`` / ``DanDomainClient`` usage in the UI layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Root of the ``ui/`` package (relative to repo root).
_UI_DIR = Path(__file__).resolve().parent.parent / "ui"

# Strings that must NOT appear anywhere in ui/ Python source files.
_FORBIDDEN_STRINGS = [
    "dandomain_api",
    "DanDomainClient",
    "Product_GetAll",
    "Product_GetVariantsByItemNumber",
    "zeep",
]


def _ui_python_files() -> list[Path]:
    """Return all .py files under the ui/ directory."""
    return sorted(_UI_DIR.rglob("*.py"))


class TestNoDirectSoapInUI:
    """UI layer must never contain direct SOAP client usage."""

    @pytest.mark.parametrize("forbidden", _FORBIDDEN_STRINGS)
    def test_forbidden_string_absent(self, forbidden: str):
        """No ui/ Python file may contain *forbidden*."""
        violations: list[str] = []
        for py_file in _ui_python_files():
            content = py_file.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(content.splitlines(), start=1):
                if forbidden in line:
                    rel = py_file.relative_to(_UI_DIR.parent)
                    violations.append(f"  {rel}:{lineno}: {line.strip()}")

        assert not violations, (
            f"Forbidden string {forbidden!r} found in ui/ files:\n"
            + "\n".join(violations)
        )

    def test_ui_directory_exists(self):
        """Sanity: the ui/ directory must exist for this scan to be meaningful."""
        assert _UI_DIR.is_dir(), f"Expected ui/ directory at {_UI_DIR}"

    def test_at_least_one_python_file(self):
        """Sanity: there must be at least one .py file under ui/."""
        assert len(_ui_python_files()) > 0, "No .py files found under ui/"
