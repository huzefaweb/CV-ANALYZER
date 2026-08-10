"""AR-4: automated check that the pure domain kernel stays framework-free.

Fails if any module under src/domain imports a web framework, database
driver, filesystem implementation, HTTP client, provider SDK, or a
concrete clock (datetime.now/date.today) instead of an injected one.
Mirrors apps/gateway/tests/test_domain_boundary.py from Story 1.2.
"""

from __future__ import annotations

import ast
from pathlib import Path

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "src" / "domain"

BANNED_IMPORT_PREFIXES = (
    "fastapi",
    "starlette",
    "sqlalchemy",
    "psycopg",
    "psycopg2",
    "alembic",
    "auth0",
    "pypdf",
    "docx",
    "python_docx",
    "httpx",
    "requests",
    "boto3",
    "openai",
)


def _iter_domain_python_files():
    return sorted(DOMAIN_DIR.rglob("*.py"))


def test_domain_files_exist():
    assert _iter_domain_python_files(), "expected at least one file under src/domain"


def test_domain_kernel_imports_no_banned_dependency():
    violations: list[str] = []
    for path in _iter_domain_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                top_level = name.split(".")[0]
                if top_level in BANNED_IMPORT_PREFIXES:
                    violations.append(f"{path}: imports banned module '{name}'")
    assert not violations, "\n".join(violations)


def test_domain_kernel_uses_no_concrete_clock():
    violations: list[str] = []
    for path in _iter_domain_python_files():
        text = path.read_text(encoding="utf-8")
        if "datetime.now(" in text or "date.today(" in text:
            violations.append(f"{path}: uses a concrete clock instead of an injected one")
    assert not violations, "\n".join(violations)
