"""Mechanical dependency rules for the feature-modular package."""

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
}
REMOVED_ROOTS = {
    "adapters",
    "application",
    "bridges",
    "domain",
    "infrastructure",
    "presentation",
    "providers",
}
REMOVED_MODULES = {
    "_local_tool_contracts",
    "_local_tool_discovery",
    "_local_tool_launcher",
    "_local_tool_policy",
    "_local_tool_process",
    "_prototype_analysis",
    "_prototype_parser",
    "_prototype_support",
    "_prototype_transcription",
    "cli",
    "evidence_collections",
    "evidence_contract",
    "evidence_loader",
    "evidence_validation",
    "evidence_validation_assertions",
    "evidence_validation_common",
    "evidence_validation_events",
    "evidence_validation_records",
    "evidence_validation_relations",
    "evidence_validation_review",
    "evidence_validation_revisions",
    "local_artifacts",
    "local_tools",
    "media_ingest",
    "network",
    "project",
    "project_store",
    "prototype_commands",
    "transcript_writers",
}
REMOVED_WORKBENCH_MODULES = {
    "_workbench_executor_config",
    "_workbench_executor_identity",
    "_workbench_processing_contracts",
    "_workbench_processing_lifecycle",
    "_workbench_processing_store",
    "_workbench_projection",
    "workbench_api",
    "workbench_http",
    "workbench_local_executor",
    "workbench_media",
    "workbench_processing",
    "workbench_protocol",
    "workbench_server",
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


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(("notewitness", *parts))


def _resolve_import(module: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = module.split(".")[:-1]
    prefix = package[: len(package) - node.level + 1]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _notewitness_imports(path: Path) -> set[str]:
    module = _module_name(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(_resolve_import(module, node))
    return {name for name in imports if name == "notewitness" or name.startswith("notewitness.")}


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
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            relative = path.relative_to(PACKAGE_ROOT)
            source_feature = relative.parts[0]
            if source_feature not in ALLOWED_DEPENDENCIES:
                continue
            for imported in sorted(_notewitness_imports(path)):
                parts = imported.split(".")
                if len(parts) < 2 or parts[1] not in FEATURES:
                    continue
                target_feature = parts[1]
                if target_feature not in ALLOWED_DEPENDENCIES[source_feature]:
                    violations.append(
                        f"{relative}: {source_feature} must not import {imported}"
                    )
        self.assertEqual([], violations, "\n".join(violations))

    def test_removed_architecture_is_not_imported(self) -> None:
        violations: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            relative = path.relative_to(PACKAGE_ROOT)
            for imported in sorted(_notewitness_imports(path)):
                parts = imported.split(".")
                if len(parts) < 2:
                    continue
                root = parts[1]
                if root in REMOVED_ROOTS or root in REMOVED_MODULES:
                    violations.append(f"{relative}: stale import {imported}")
        self.assertEqual([], violations, "\n".join(violations))

    def test_removed_workbench_modules_are_not_imported(self) -> None:
        violations: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            relative = path.relative_to(PACKAGE_ROOT)
            for imported in sorted(_notewitness_imports(path)):
                parts = imported.split(".")
                if (
                    len(parts) >= 3
                    and parts[1] == "workbench"
                    and parts[2] in REMOVED_WORKBENCH_MODULES
                ):
                    violations.append(f"{relative}: stale import {imported}")
        self.assertEqual([], violations, "\n".join(violations))

    def test_interfaces_are_not_imported_by_features(self) -> None:
        violations: list[str] = []
        for feature in FEATURES[:-1]:
            for path in sorted((PACKAGE_ROOT / feature).rglob("*.py")):
                for imported in sorted(_notewitness_imports(path)):
                    if imported.startswith("notewitness.interfaces"):
                        violations.append(
                            f"{path.relative_to(PACKAGE_ROOT)} imports {imported}"
                        )
        self.assertEqual([], violations, "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
