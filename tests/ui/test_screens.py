"""
Tests for UI pages compliance (Stream C).
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.ui.format import scan_forbidden_words


def test_ui_files_valid_python_syntax():
    ui_dir = Path("app/ui")
    py_files = list(ui_dir.rglob("*.py"))
    assert len(py_files) >= 4, f"Expected at least 4 UI python files, found {len(py_files)}"

    for f in py_files:
        code = f.read_text(encoding="utf-8")
        # Ensure it parses cleanly
        tree = ast.parse(code, filename=str(f))
        assert tree is not None


def test_ui_contains_no_forbidden_phrases():
    ui_dir = Path("app/ui")
    py_files = list(ui_dir.rglob("*.py"))

    for f in py_files:
        if f.name == "format.py":
            continue  # format.py defines the FORBIDDEN_PHRASES list
        code = f.read_text(encoding="utf-8")
        found = scan_forbidden_words(code)
        assert not found, f"Forbidden phrases found in {f}: {found}"


def test_ui_does_not_import_engine_or_data():
    """UI must call ONLY the FastAPI and never import engine or data code."""
    ui_dir = Path("app/ui")
    py_files = list(ui_dir.rglob("*.py"))

    for f in py_files:
        code = f.read_text(encoding="utf-8")
        tree = ast.parse(code, filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("app.engine"), f"{f} imports engine directly: {alias.name}"
                    assert not alias.name.startswith("app.data"), f"{f} imports data directly: {alias.name}"
                    assert not alias.name.startswith("app.db"), f"{f} imports db directly: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert not node.module.startswith("app.engine"), f"{f} imports engine directly: {node.module}"
                    assert not node.module.startswith("app.data"), f"{f} imports data directly: {node.module}"
                    assert not node.module.startswith("app.db"), f"{f} imports db directly: {node.module}"

