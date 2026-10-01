from __future__ import annotations

import ast
import json
from pathlib import Path
import tomllib
import unittest

from scripts.verify_public_hygiene import candidate_paths


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "notewitness"
NETWORK_CAPABLE_MODULES = {
    "aiohttp",
    "ctypes",
    "ftplib",
    "http",
    "httpx",
    "requests",
    "smtplib",
    "socket",
    "ssl",
    "subprocess",
    "urllib",
}
# Reviewed boundary modules, relative to src/notewitness.
ALLOWED_RUNTIME_IMPORTS = {
    "lessons/network.py": NETWORK_CAPABLE_MODULES,
    "analysis/local_tools/__init__.py": {"subprocess"},
    "analysis/local_tools/process.py": {"subprocess"},
    "workbench/api.py": {"http", "urllib"},
    "workbench/http.py": {"http", "urllib"},
    "workbench/media.py": {"http", "urllib"},
    "workbench/protocol.py": {"http", "urllib"},
}


class RepositoryContractTests(unittest.TestCase):
    def test_production_dependency_list_is_empty(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual([], metadata["project"]["dependencies"])

    def test_local_provider_bridges_are_packaged_as_console_scripts(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual("notewitness", metadata["project"]["name"])
        self.assertEqual(
            "notewitness.interfaces.cli.main:main",
            metadata["project"]["scripts"]["notewitness"],
        )
        self.assertEqual(
            "notewitness.interfaces.bridges.dispatcher:main",
            metadata["project"]["scripts"]["notewitness-provider-bridge"],
        )
        self.assertEqual(
            "notewitness.interfaces.bridges.mt3_decoded_events_bridge:main",
            metadata["project"]["scripts"]["notewitness-mt3-events-bridge"],
        )
        self.assertEqual(
            ["src/notewitness"],
            metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"],
        )

    def test_notewitness_brand_and_protocol_identifiers_are_consistent(self) -> None:
        schema = json.loads(
            (ROOT / "schemas/v0.1/evidence-graph.schema.json").read_text(encoding="utf-8")
        )
        context = json.loads(
            (ROOT / "schemas/v0.1/context.jsonld").read_text(encoding="utf-8")
        )["@context"]
        assets = PACKAGE / "workbench" / "assets"
        index = (assets / "index.html").read_text(encoding="utf-8")

        self.assertEqual("urn:notewitness:schema:evidence-graph:0.1.0", schema["$id"])
        self.assertEqual("NoteWitness evidence graph", schema["title"])
        self.assertEqual("urn:notewitness:vocabulary:", context["nw"])
        self.assertNotIn("mt", context)
        self.assertIn("NoteWitness: local evidence workbench", index)
        self.assertIn("/assets/notewitness-mark.svg", index)
        self.assertTrue((assets / "notewitness-mark.svg").is_file())

    def test_legacy_brand_has_no_repository_content(self) -> None:
        legacy = "music" + "transcript"
        text_suffixes = {
            ".css", ".html", ".js", ".json", ".jsonld", ".md", ".mjs",
            ".py", ".sh", ".svg", ".toml", ".txt", ".yaml", ".yml",
        }
        offenders: list[str] = []
        for relative in candidate_paths():
            path = ROOT / relative
            if not path.is_file():
                continue
            if path.suffix not in text_suffixes and path.name not in {
                ".editorconfig", "LICENSE"
            }:
                continue
            contents = path.read_text(encoding="utf-8", errors="ignore")
            if legacy in contents.casefold():
                offenders.append(str(path.relative_to(ROOT)))

        self.assertEqual([], offenders)

    def test_only_reviewed_boundary_modules_import_runtime_primitives(self) -> None:
        offenders: list[str] = []
        for path in PACKAGE.rglob("*.py"):
            contents = path.read_text(encoding="utf-8")
            tree = ast.parse(contents, filename=str(path))
            imported_roots: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(
                        alias.name.partition(".")[0] for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    imported_roots.add(node.module.partition(".")[0])
            dynamic_import_tokens = ("__import__", "import_module(", "os.system(")
            relative = path.relative_to(PACKAGE).as_posix()
            forbidden_roots = (
                imported_roots & NETWORK_CAPABLE_MODULES
            ) - ALLOWED_RUNTIME_IMPORTS.get(relative, set())
            if forbidden_roots or any(
                token in contents for token in dynamic_import_tokens
            ):
                offenders.append(relative)

        self.assertEqual([], offenders)

    def test_evidence_internals_do_not_import_the_public_facade(self) -> None:
        offenders: list[str] = []
        for path in (PACKAGE / "core" / "evidence").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "notewitness.evidence"
                ):
                    offenders.append(str(path.relative_to(ROOT)))
                    break

        self.assertEqual([], offenders)

    def test_package_initializers_do_not_re_export(self) -> None:
        allowed = {
            PACKAGE / "__init__.py",
            PACKAGE / "analysis" / "local_tools" / "__init__.py",
        }
        offenders: list[str] = []
        for path in PACKAGE.rglob("__init__.py"):
            if path in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if any(
                isinstance(node, (ast.Import, ast.ImportFrom))
                for node in ast.walk(tree)
            ):
                offenders.append(str(path.relative_to(ROOT)))

        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
