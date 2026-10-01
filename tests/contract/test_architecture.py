"""Mechanical dependency rules for the layered feature packages."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "notewitness"
FEATURES = ("core", "projects", "analysis", "lessons", "workbench", "interfaces")
ALLOWED_DEPENDENCIES = {
    "core": {"core"},
    "projects": {"core", "projects"},
    "analysis": {"core", "projects", "analysis"},
    "lessons": {"core", "projects", "analysis", "lessons"},
    "workbench": {"core", "projects", "analysis", "lessons", "workbench"},
    "interfaces": set(FEATURES),
    # Root modules: the public evidence API and the ``python -m`` entry point.
    "evidence": {"core", "projects"},
    "__main__": {"interfaces"},
    "__init__": set(),
}
CORE_IO_MODULES = {
    "http",
    "os",
    "shutil",
    "socket",
    "sqlite3",
    "subprocess",
    "tempfile",
    "urllib",
}
# Executed by file path under a trusted base interpreter (possibly the system
# Python 3.9), so it cannot import NoteWitness or rely on newer syntax.
SELF_CONTAINED_LAUNCHER = PACKAGE_ROOT / "analysis" / "local_tools" / "launcher.py"


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(("notewitness", *parts))


def _resolve_import(path: Path, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = _module_name(path).split(".")
    if path.name != "__init__.py":
        package.pop()
    prefix = package[: len(package) - node.level + 1]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _notewitness_imports(path: Path) -> list[tuple[str, tuple[str, ...]]]:
    """Return each imported notewitness module with the names taken from it."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names = tuple(alias.name for alias in node.names)
            imports.append((_resolve_import(path, node), names))
    return [
        (module, names)
        for module, names in imports
        if module == "notewitness" or module.startswith("notewitness.")
    ]


def _module_path(module: str) -> Path | None:
    parts = module.split(".")[1:]
    if not parts:
        return PACKAGE_ROOT / "__init__.py"
    relative = Path(*parts)
    for candidate in (PACKAGE_ROOT / relative.with_suffix(".py"), PACKAGE_ROOT / relative / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _top_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".", 1)[0] for alias in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                names.update(item.id for item in ast.walk(target) if isinstance(item, ast.Name))
    return names


def _source_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _feature(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT)
    return relative.parts[0] if len(relative.parts) > 1 else relative.stem


class ArchitectureTests(unittest.TestCase):
    def test_core_has_no_io_dependencies(self) -> None:
        violations: list[str] = []
        for path in sorted((PACKAGE_ROOT / "core").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in CORE_IO_MODULES:
                            violations.append(f"{path.name}: imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = (node.module or "").split(".", 1)[0]
                    if module in CORE_IO_MODULES:
                        violations.append(f"{path.name}: imports {node.module}")
                    if node.module == "pathlib" and any(
                        alias.name == "Path" for alias in node.names
                    ):
                        violations.append(f"{path.name}: imports pathlib.Path")
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "open"
                ):
                    violations.append(f"{path.name}: calls open()")
        self.assertEqual([], violations, "\n".join(violations))

    def test_feature_dependency_direction(self) -> None:
        violations: list[str] = []
        for path in _source_files():
            source = _feature(path)
            self.assertIn(source, ALLOWED_DEPENDENCIES, f"unclassified module {path}")
            for imported, _ in _notewitness_imports(path):
                parts = imported.split(".")
                if len(parts) < 2:
                    if source != "interfaces":
                        violations.append(f"{path.relative_to(PACKAGE_ROOT)}: imports notewitness root")
                    continue
                target = parts[1]
                if target in FEATURES and target not in ALLOWED_DEPENDENCIES[source]:
                    violations.append(
                        f"{path.relative_to(PACKAGE_ROOT)}: {source} must not import {imported}"
                    )
        self.assertEqual([], violations, "\n".join(violations))

    def test_every_internal_import_resolves(self) -> None:
        violations: list[str] = []
        for path in _source_files():
            for imported, names in _notewitness_imports(path):
                module_path = _module_path(imported)
                if module_path is None:
                    violations.append(f"{path.relative_to(PACKAGE_ROOT)}: missing module {imported}")
                    continue
                bound = _top_level_names(module_path)
                for name in names:
                    if name != "*" and name not in bound and _module_path(f"{imported}.{name}") is None:
                        violations.append(
                            f"{path.relative_to(PACKAGE_ROOT)}: {imported} does not define {name}"
                        )
        self.assertEqual([], violations, "\n".join(violations))

    def test_private_modules_are_imported_only_within_their_package(self) -> None:
        violations: list[str] = []
        for path in _source_files():
            for imported, names in _notewitness_imports(path):
                candidates = [imported, *(f"{imported}.{name}" for name in names)]
                for candidate in candidates:
                    target = _module_path(candidate)
                    if target is None or not target.name.startswith("_") or target.name.startswith("__"):
                        continue
                    if target.parent != path.parent:
                        violations.append(
                            f"{path.relative_to(PACKAGE_ROOT)}: imports private module {candidate}"
                        )
        self.assertEqual([], violations, "\n".join(violations))

    def test_local_tool_launcher_is_self_contained(self) -> None:
        source = SELF_CONTAINED_LAUNCHER.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SELF_CONTAINED_LAUNCHER), feature_version=(3, 9))
        imported = [
            alias.name if isinstance(node, ast.Import) else (node.module or "")
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in (node.names if isinstance(node, ast.Import) else node.names[:1])
        ]
        relative = [
            node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level
        ]
        self.assertEqual([], relative)
        self.assertEqual([], [name for name in imported if name.split(".", 1)[0] == "notewitness"])


if __name__ == "__main__":
    unittest.main()
