"""Import boundaries (CONTRIBUTING "The core is useful alone", AGENTS.md rule 4): only the system module
starts processes, no module imports another honeworks package, and OS probes load lazily."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "hone_drive"
PROBES = {"probe_linux", "probe_macos", "probe_windows"}


def imports(path: Path) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [(alias.name, node.col_offset) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(((node.module or ""), node.col_offset))
    return found


def modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_only_the_system_module_starts_processes() -> None:
    users = sorted(p.name for p in modules() if any(m == "subprocess" for m, _ in imports(p)))
    assert users == ["system.py"]


def test_no_sibling_packages() -> None:
    for path in modules():
        for module, _ in imports(path):
            assert not (module.startswith("hone_") and not module.startswith("hone_drive")), (
                path.name,
                module,
            )


def test_os_probes_are_imported_only_inside_functions() -> None:
    for path in modules():
        if path.stem in PROBES:
            continue
        for module, column in imports(path):
            if module.rsplit(".", 1)[-1] in PROBES:
                assert column > 0, (path.name, module)
