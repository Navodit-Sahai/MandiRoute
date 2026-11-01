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


def test_ui_entry_file_is_home():
    assert Path("app/ui/home.py").exists(), "app/ui/home.py must exist as UI entry point"
    assert not Path("app/ui/app.py").exists(), "app/ui/app.py must NOT exist"


def test_ui_screens_required_elements():
    plan_code = Path("app/ui/pages/1_Plan.py").read_text(encoding="utf-8")
    assert "Re-plan with closing times" in plan_code
    assert "Clear deductions" in plan_code
    assert "Demo defaults (user input, not observed). Replace with your actual rates." in plan_code
    assert "Closing time is a user input; leave blank if unknown." in plan_code
    assert "format_source_time" in plan_code

    compare_code = Path("app/ui/pages/2_Compare.py").read_text(encoding="utf-8")
    assert "Data notes" in compare_code
    assert "format_location_precision" in compare_code
    assert "format_kind_tag" in compare_code
    assert "[user_input]" in compare_code

    dispatch_path = Path("app/ui/pages/3_Dispatch.py")
    if not dispatch_path.exists():
        dispatch_path = Path("app/ui/legacy/3_Dispatch.py")
    dispatch_code = dispatch_path.read_text(encoding="utf-8")
    assert "Approve Dispatch" in dispatch_code
    assert "Reject Dispatch" in dispatch_code
    assert "Delayed +30 min" in dispatch_code
    assert "Delayed +60 min" in dispatch_code
    assert "Arrived at Mandi" in dispatch_code
    assert "Run watchdog tick now" in dispatch_code
    assert "Run tick with fresh data" in dispatch_code
    assert "Approve Re-plan" in dispatch_code
    assert "Keep Current Plan" in dispatch_code
    assert "WHAT_IF_TITLE" in dispatch_code
    assert "WHAT_IF_TAG" in dispatch_code
    assert "Watchdog Events Timeline" in dispatch_code



