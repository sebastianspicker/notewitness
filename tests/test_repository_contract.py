from __future__ import annotations

import ast
import json
from pathlib import Path
import tomllib
import unittest

from scripts.verify_public_hygiene import candidate_paths


ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
    def test_production_dependency_list_is_empty(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual([], metadata["project"]["dependencies"])

    def test_local_provider_bridges_are_packaged_as_console_scripts(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual("notewitness", metadata["project"]["name"])
        self.assertEqual(
            "notewitness.cli:main",
            metadata["project"]["scripts"]["notewitness"],
        )
        self.assertEqual(
            "notewitness.bridges.dispatcher:main",
            metadata["project"]["scripts"]["notewitness-provider-bridge"],
        )
        self.assertEqual(
            "notewitness.bridges.mt3_decoded_events_bridge:main",
            metadata["project"]["scripts"]["notewitness-mt3-events-bridge"],
        )
        self.assertEqual(
            ["src/notewitness"],
            metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"],
        )

    def test_notewitness_brand_and_protocol_identifiers_are_consistent(self) -> None:
        schema = json.loads(
            (ROOT / "schemas/v0.1/evidence-graph.schema.json").read_text(
                encoding="utf-8"
            )
        )
        context = json.loads(
            (ROOT / "schemas/v0.1/context.jsonld").read_text(encoding="utf-8")
        )["@context"]
        index = (
            ROOT
            / "src/notewitness/presentation/workbench_assets/index.html"
        ).read_text(encoding="utf-8")

        self.assertEqual(
            "urn:notewitness:schema:evidence-graph:0.1.0", schema["$id"]
        )
        self.assertEqual("NoteWitness evidence graph", schema["title"])
        self.assertEqual("urn:notewitness:vocabulary:", context["nw"])
        self.assertNotIn("mt", context)
        self.assertIn("NoteWitness: local evidence workbench", index)
        self.assertIn("/assets/notewitness-mark.svg", index)
        self.assertTrue(
            (
                ROOT
                / "src/notewitness/presentation/workbench_assets/notewitness-mark.svg"
            ).is_file()
        )

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
        network_capable_modules = {
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
        allowed_roots = {
            "network.py": network_capable_modules,
            "local_tools.py": {"subprocess"},
            "workbench_server.py": {"http", "urllib"},
        }
        offenders: list[str] = []
        for path in (ROOT / "src" / "notewitness").rglob("*.py"):
            contents = path.read_text(encoding="utf-8")
            tree = ast.parse(contents, filename=str(path))
            imported_roots: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(
                        alias.name.partition(".")[0] for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_roots.add(node.module.partition(".")[0])
            dynamic_import_tokens = ("__import__", "import_module(", "os.system(")
            forbidden_roots = (
                imported_roots & network_capable_modules
            ) - allowed_roots.get(path.name, set())
            if forbidden_roots or any(
                token in contents for token in dynamic_import_tokens
            ):
                offenders.append(str(path.relative_to(ROOT)))

        self.assertEqual([], offenders)

    def test_evidence_internals_do_not_import_the_public_facade(self) -> None:
        offenders: list[str] = []
        for path in (ROOT / "src" / "notewitness").glob("evidence_*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "notewitness.evidence"
                ):
                    offenders.append(str(path.relative_to(ROOT)))
                    break

        self.assertEqual([], offenders)

    def test_transcription_facade_stays_thin_and_acyclic(self) -> None:
        facade = ROOT / "src" / "notewitness" / "domain" / "transcription.py"
        self.assertLessEqual(len(facade.read_text(encoding="utf-8").splitlines()), 120)

        offenders: list[str] = []
        for path in facade.parent.glob("transcription_*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "notewitness.domain.transcription"
                ):
                    offenders.append(str(path.relative_to(ROOT)))
                    break

        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
