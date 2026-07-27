from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pickle
from tempfile import TemporaryDirectory
import unittest

from notewitness.evidence import (
    CORE_RELATION_TYPES,
    MAX_PROJECT_BYTES,
    EvidenceGraph,
    EvidenceGraphError,
    ValidationIssue,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "synthetic_lesson" / "project.json"


class EvidenceGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_synthetic_fixture_is_valid_and_complete(self) -> None:
        graph = EvidenceGraph(self.payload)

        self.assertEqual((), graph.validate())
        relation_types = {
            relation["type"] for relation in graph.records("relations")
        }
        self.assertTrue(CORE_RELATION_TYPES.issubset(relation_types))
        self.assertIn("local:assigned_for_practice", relation_types)
        self.assertIn("C♯", graph.index("events")["event:instruction"]["body"]["value"])
        self.assertEqual(
            "not_alignable",
            graph.index("targets")["target:humming"]["alignment_state"],
        )

    def test_public_error_types_keep_their_compatibility_path(self) -> None:
        issue = ValidationIssue("$.project", "must be an object")

        self.assertEqual("notewitness.evidence", EvidenceGraphError.__module__)
        self.assertEqual("notewitness.evidence", ValidationIssue.__module__)
        self.assertEqual(issue, pickle.loads(pickle.dumps(issue)))
        self.assertIs(
            EvidenceGraphError,
            pickle.loads(pickle.dumps(EvidenceGraphError)),
        )

    def test_fixture_source_checksum_matches(self) -> None:
        source_path = FIXTURE_PATH.parent / "script.txt"
        expected = self.payload["sources"][0]["sha256"]

        actual = hashlib.sha256(source_path.read_bytes()).hexdigest()

        self.assertEqual(expected, actual)

    def test_evidence_event_requires_a_target(self) -> None:
        payload = deepcopy(self.payload)
        payload["events"][0]["target_ids"] = []

        issues = EvidenceGraph(payload).validate()

        self.assertTrue(
            any("evidence events require at least one target" in issue.message for issue in issues)
        )

    def test_project_scoped_event_may_have_no_target(self) -> None:
        payload = deepcopy(self.payload)
        payload["events"][0]["scope"] = "project"
        payload["events"][0]["target_ids"] = []

        issues = EvidenceGraph(payload).validate()

        self.assertFalse(any("target" in issue.message for issue in issues))
        self.assertFalse(
            EvidenceGraph(payload).selected_events_allow_remote(
                [payload["events"][0]["id"]]
            )
        )

    def test_duplicate_ids_across_record_types_fail(self) -> None:
        payload = deepcopy(self.payload)
        payload["actors"][0]["id"] = payload["rights"][0]["id"]

        issues = EvidenceGraph(payload).validate()

        self.assertTrue(any("duplicates" in issue.message for issue in issues))

    def test_unknown_relation_reference_fails(self) -> None:
        payload = deepcopy(self.payload)
        payload["relations"][0]["arguments"][0]["ref_id"] = "event:missing"

        issues = EvidenceGraph(payload).validate()

        self.assertTrue(any("unknown event" in issue.message for issue in issues))

    def test_derived_rights_cannot_exceed_source_rights(self) -> None:
        payload = deepcopy(self.payload)
        payload["rights"].append(
            {
                "id": "rights:broader",
                "access": "public",
                "remote_processing": True,
                "model_training": False,
                "retention": "retain",
            }
        )
        payload["events"][0]["rights_id"] = "rights:broader"

        issues = EvidenceGraph(payload).validate()

        self.assertTrue(any("broader" in issue.message for issue in issues))

    def test_derived_rights_must_preserve_retention(self) -> None:
        payload = deepcopy(self.payload)
        payload["rights"].append(
            {
                "id": "rights:different-retention",
                "access": "public",
                "remote_processing": False,
                "model_training": False,
                "retention": "delete-after-session",
            }
        )
        payload["events"][0]["rights_id"] = "rights:different-retention"

        issues = EvidenceGraph(payload).validate()

        self.assertTrue(any("broader" in issue.message for issue in issues))

    def test_machine_generator_cannot_mint_human_acceptance(self) -> None:
        payload = deepcopy(self.payload)
        payload["generators"].append(
            {
                "id": "generator:machine",
                "kind": "machine",
                "name": "Synthetic machine",
                "version": "1",
                "model": "fixture-model",
                "weight_hash_state": "known",
            }
        )
        payload["events"][0]["generator_id"] = "generator:machine"
        payload["events"][0]["review_status"] = "human_accepted"

        issues = EvidenceGraph(payload).validate()

        self.assertTrue(
            any(
                "machine-generated records must remain machine_suggested"
                in issue.message
                for issue in issues
            )
        )

    def test_human_acceptance_requires_adjudication_revision(self) -> None:
        payload = deepcopy(self.payload)
        payload["events"][0]["review_status"] = "human_accepted"

        issues = EvidenceGraph(payload).validate()

        self.assertTrue(
            any("requires a human adjudication revision" in issue.message for issue in issues)
        )

    def test_human_acceptance_with_adjudication_revision_is_valid(self) -> None:
        payload = deepcopy(self.payload)
        payload["events"][0]["review_status"] = "human_accepted"
        payload["revisions"].append(
            {
                "id": "revision:instruction-adjudicated",
                "record_id": "event:instruction",
                "parent_revision_ids": [],
                "author_id": "actor:researcher",
                "timestamp": "2026-07-18T00:01:00+00:00",
                "operation": "adjudicate",
                "reason": "Human review of a prior suggestion",
            }
        )

        self.assertEqual((), EvidenceGraph(payload).validate())

    def test_runtime_rejects_security_relevant_schema_shape_errors(self) -> None:
        mutations = {
            "unknown field": lambda payload: payload["events"][0].update(
                {"unexpected": True}
            ),
            "invalid visibility": lambda payload: payload["actors"][0].update(
                {"visibility": "internet"}
            ),
            "invalid layer": lambda payload: payload["events"][0].update(
                {"layer": "trusted"}
            ),
            "invalid body": lambda payload: payload["events"][0].update(
                {"body": "not-an-object"}
            ),
            "unhashable status": lambda payload: payload["events"][0].update(
                {"review_status": {"forged": True}}
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                payload = deepcopy(self.payload)
                mutate(payload)
                self.assertNotEqual((), EvidenceGraph(payload).validate())

    def test_load_rejects_duplicate_keys_and_resource_exhaustion_inputs(self) -> None:
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            duplicate = directory / "duplicate.json"
            duplicate.write_text(
                '{"schema_version":"0.1.0","schema_version":"0.1.0"}',
                encoding="utf-8",
            )
            oversized = directory / "oversized.json"
            oversized.write_bytes(b" " * (MAX_PROJECT_BYTES + 1))
            nested = directory / "nested.json"
            nested.write_text(
                '{"x":' + "[" * 70 + "0" + "]" * 70 + "}",
                encoding="utf-8",
            )

            for path in (duplicate, oversized, nested):
                with self.subTest(path=path.name):
                    with self.assertRaises(EvidenceGraphError):
                        EvidenceGraph.load(path)


if __name__ == "__main__":
    unittest.main()
