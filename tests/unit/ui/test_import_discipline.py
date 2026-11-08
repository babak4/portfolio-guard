"""UI modules reach data only through the read API and the actions."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

UI = Path(__file__).resolve().parents[3] / "src" / "guard" / "ui"
ALLOWED = (
    "streamlit",
    "guard.readapi",
    "guard.actions",
    "guard.report",
    "guard.config",
    "guard.paths",
    "guard.ui",
)
FORBIDDEN_STDLIB = {"sqlite3"}


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "guard":  # "from guard import x" imports the module guard.x
                names += [f"guard.{a.name}" for a in node.names]
            else:
                names.append(node.module)
    return names


def _allowed(name: str) -> bool:
    top = name.split(".")[0]
    if top in FORBIDDEN_STDLIB:
        return False
    if top in sys.stdlib_module_names or top == "__future__":
        return True
    return any(name == a or name.startswith(a + ".") for a in ALLOWED)


def test_ui_imports_only_the_allowed_modules() -> None:
    files = sorted(UI.rglob("*.py"))
    assert len(files) >= 8  # the scan really covers the app, auth, components and pages
    bad = {f.name: [n for n in _imports(f) if not _allowed(n)] for f in files}
    assert {k: v for k, v in bad.items() if v} == {}


def test_the_checker_rejects_what_it_should(tmp_path: Path) -> None:
    for name in (
        "sqlite3",
        "duckdb",
        "guard.storage",
        "guard.storage.run_store",
        "guard.run.replay",
        "pandas",
    ):
        assert not _allowed(name), name
    for name in ("streamlit", "guard.readapi.models", "guard.actions", "guard.ui.auth", "datetime"):
        assert _allowed(name), name
