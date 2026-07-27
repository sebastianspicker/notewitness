from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from notewitness.evidence import EvidenceGraph
from notewitness.network import (
    NetworkAccessDenied,
    NetworkMode,
    OpenAIHTTPTransport,
    OPENAI_RESPONSES_URL,
)
from notewitness.providers.openai_responses import (
    OpenAIConfigurationError,
    OpenAIOutputError,
    OpenAIRelationSuggester,
    OpenAISettings,
)
from tests.support.http_fakes import FakeOpener, FakeResponse


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "synthetic_lesson" / "project.json"


class PoisonEnvironment(dict[str, str]):
    def get(self, key: str, default: str = "") -> str:
        raise AssertionError("credentials were consulted before policy denial")


def remote_graph() -> EvidenceGraph:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["network"]["mode"] = "remote_explicit"
    payload["rights"][0]["remote_processing"] = True
    graph = EvidenceGraph(payload)
    graph.require_valid()
    return graph


def completed_response(structured_output: object) -> dict[str, object]:
    return {
        "id": "resp_test",
        "status": "completed",
        "error": None,
        "model": "test-model-snapshot",
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(structured_output),
                    }
                ],
            },
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


class OpenAIResponsesTests(unittest.TestCase):
    def test_only_notewitness_model_environment_variable_is_accepted(self) -> None:
        legacy_model_key = "MUSIC" + "TRANSCRIPT_OPENAI_MODEL"
        with self.assertRaisesRegex(
            OpenAIConfigurationError,
            "NOTEWITNESS_OPENAI_MODEL is not configured",
        ):
            OpenAISettings.from_environment(
                {
                    "OPENAI_API_KEY": "test-key",
                    legacy_model_key: "legacy-model",
                }
            )

        settings = OpenAISettings.from_environment(
            {
                "OPENAI_API_KEY": "test-key",
                "NOTEWITNESS_OPENAI_MODEL": "test-model",
            }
        )
        self.assertEqual("test-model", settings.model)

    def test_offline_denial_happens_before_credential_lookup(self) -> None:
        with self.assertRaises(NetworkAccessDenied):
            graph = remote_graph()
            graph.payload["network"]["mode"] = NetworkMode.OFFLINE.value
            OpenAIRelationSuggester.suggest_authorized(
                graph=graph,
                event_ids=["event:instruction", "event:demonstration"],
                confirmed=True,
                environment=PoisonEnvironment(),
            )

    def test_selected_event_rights_are_checked_before_credential_lookup(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["network"]["mode"] = "remote_explicit"
        graph = EvidenceGraph(payload)
        graph.require_valid()

        with self.assertRaises(NetworkAccessDenied):
            OpenAIRelationSuggester.suggest_authorized(
                graph=graph,
                event_ids=["event:instruction", "event:demonstration"],
                confirmed=True,
                environment=PoisonEnvironment(),
            )

    def test_targetless_project_event_is_denied_before_credential_lookup(self) -> None:
        graph = remote_graph()
        graph.payload["events"][0]["scope"] = "project"
        graph.payload["events"][0]["target_ids"] = []
        graph.require_valid()

        with self.assertRaises(NetworkAccessDenied):
            OpenAIRelationSuggester.suggest_authorized(
                graph=graph,
                event_ids=["event:instruction"],
                confirmed=True,
                environment=PoisonEnvironment(),
            )

    def test_request_is_minimized_structured_and_not_stored(self) -> None:
        graph = remote_graph()
        before = deepcopy(graph.payload)
        structured = {
            "suggestions": [
                {
                    "relation_type": "feedback_on",
                    "arguments": [
                        {"role": "feedback", "event_ref": "e0"},
                        {"role": "attempt", "event_ref": "e1"},
                    ],
                    "rationale": "The feedback follows the first attempt.",
                }
            ]
        }
        response_body = json.dumps(completed_response(structured)).encode("utf-8")
        response_payload = json.loads(response_body)
        response_payload["usage"]["provider_echo"] = "selected source text"
        response_body = json.dumps(response_payload).encode("utf-8")
        opener = FakeOpener(FakeResponse(response_body))
        transport = OpenAIHTTPTransport(opener)
        result = OpenAIRelationSuggester.suggest_authorized(
            graph=graph,
            event_ids=["event:feedback", "event:attempt-1"],
            confirmed=True,
            environment={
                "OPENAI_API_KEY": "test-key",
                "NOTEWITNESS_OPENAI_MODEL": "test-model",
            },
            transport=transport,
        )

        self.assertEqual(before, graph.payload)
        self.assertEqual("machine_suggested", result.as_dict()["review_status"])
        self.assertEqual("feedback_on", result.suggestions[0].relation_type)
        self.assertEqual(
            "event:feedback", result.suggestions[0].arguments[0].event_id
        )
        self.assertEqual(1, opener.calls)
        self.assertEqual(OPENAI_RESPONSES_URL, opener.request.full_url)
        self.assertEqual("Bearer test-key", opener.request.get_header("Authorization"))
        request_payload = json.loads(opener.request.data)
        self.assertIs(False, request_payload["store"])
        self.assertEqual("json_schema", request_payload["text"]["format"]["type"])
        minimized_input = json.loads(request_payload["input"])
        self.assertEqual({"ref", "text"}, set(minimized_input["events"][0]))
        self.assertEqual("e0", minimized_input["events"][0]["ref"])
        self.assertNotIn("event:feedback", request_payload["input"])
        self.assertNotIn("type", minimized_input["events"][0])
        self.assertNotIn("actor_id", request_payload["input"])
        self.assertNotIn("source_id", request_payload["input"])
        self.assertNotIn("rationale", result.as_safe_dict()["suggestions"][0])
        self.assertNotIn("provider_echo", result.as_safe_dict()["usage"])

    def test_preview_uses_aliases_and_hides_text_by_default(self) -> None:
        graph = remote_graph()

        projection = OpenAIRelationSuggester.preview(
            graph=graph,
            event_ids=["event:instruction", "event:demonstration"],
        )

        safe_preview = projection.preview_dict()
        detailed_preview = projection.preview_dict(include_text=True)
        self.assertEqual("e0", safe_preview["events"][0]["ref"])
        self.assertNotIn("text", safe_preview["events"][0])
        self.assertIn("text", detailed_preview["events"][0])
        self.assertNotIn("event:instruction", projection.input_json)

    def test_unselected_event_reference_is_rejected(self) -> None:
        graph = remote_graph()
        structured = {
            "suggestions": [
                {
                    "relation_type": "revises",
                    "arguments": [
                        {"role": "revision", "event_ref": "e0"},
                        {"role": "earlier_attempt", "event_ref": "e99"},
                    ],
                    "rationale": "Invalid reference.",
                }
            ]
        }
        opener = FakeOpener(
            FakeResponse(json.dumps(completed_response(structured)).encode("utf-8"))
        )
        with self.assertRaisesRegex(OpenAIOutputError, "unselected"):
            OpenAIRelationSuggester.suggest_authorized(
                graph=graph,
                event_ids=["event:attempt-2", "event:attempt-1"],
                confirmed=True,
                environment={
                    "OPENAI_API_KEY": "test-key",
                    "NOTEWITNESS_OPENAI_MODEL": "test-model",
                },
                transport=OpenAIHTTPTransport(opener),
            )

    def test_provider_cannot_set_human_acceptance(self) -> None:
        graph = remote_graph()
        structured = {
            "suggestions": [
                {
                    "relation_type": "revises",
                    "arguments": [
                        {"role": "revision", "event_ref": "e0"},
                        {"role": "earlier_attempt", "event_ref": "e1"},
                    ],
                    "rationale": "Attempt changed.",
                    "review_status": "human_accepted",
                }
            ]
        }
        opener = FakeOpener(
            FakeResponse(json.dumps(completed_response(structured)).encode("utf-8"))
        )
        with self.assertRaisesRegex(OpenAIOutputError, "unexpected shape"):
            OpenAIRelationSuggester.suggest_authorized(
                graph=graph,
                event_ids=["event:attempt-2", "event:attempt-1"],
                confirmed=True,
                environment={
                    "OPENAI_API_KEY": "test-key",
                    "NOTEWITNESS_OPENAI_MODEL": "test-model",
                },
                transport=OpenAIHTTPTransport(opener),
            )

    def test_safe_output_cannot_include_provider_controlled_source_echoes(self) -> None:
        graph = remote_graph()
        source_text = graph.index("events")["event:instruction"]["body"]["value"]
        response = completed_response({"suggestions": []})
        response["model"] = source_text
        opener = FakeOpener(FakeResponse(json.dumps(response).encode("utf-8")))

        result = OpenAIRelationSuggester.suggest_authorized(
            graph=graph,
            event_ids=["event:instruction", "event:demonstration"],
            confirmed=True,
            environment={
                "OPENAI_API_KEY": "test-key",
                "NOTEWITNESS_OPENAI_MODEL": "test-model",
            },
            transport=OpenAIHTTPTransport(opener),
        )

        safe_output = json.dumps(result.as_safe_dict(), ensure_ascii=False)
        self.assertNotIn(source_text, safe_output)
        self.assertNotIn("response_id", result.as_safe_dict())
        self.assertNotIn("returned_model", result.as_safe_dict())

        echoed_id_response = completed_response({"suggestions": []})
        echoed_id_response["id"] = source_text
        echoed_id_opener = FakeOpener(
            FakeResponse(json.dumps(echoed_id_response).encode("utf-8"))
        )
        with self.assertRaisesRegex(OpenAIOutputError, "response ID"):
            OpenAIRelationSuggester.suggest_authorized(
                graph=graph,
                event_ids=["event:instruction", "event:demonstration"],
                confirmed=True,
                environment={
                    "OPENAI_API_KEY": "test-key",
                    "NOTEWITNESS_OPENAI_MODEL": "test-model",
                },
                transport=OpenAIHTTPTransport(echoed_id_opener),
            )

    def test_provider_roles_are_fixed_by_relation_vocabulary(self) -> None:
        graph = remote_graph()
        source_text = graph.index("events")["event:instruction"]["body"]["value"]
        structured = {
            "suggestions": [
                {
                    "relation_type": "feedback_on",
                    "arguments": [
                        {"role": source_text, "event_ref": "e0"},
                        {"role": "attempt", "event_ref": "e1"},
                    ],
                    "rationale": "Echo attempt.",
                }
            ]
        }
        opener = FakeOpener(
            FakeResponse(json.dumps(completed_response(structured)).encode("utf-8"))
        )

        with self.assertRaisesRegex(OpenAIOutputError, "semantic roles"):
            OpenAIRelationSuggester.suggest_authorized(
                graph=graph,
                event_ids=["event:feedback", "event:attempt-1"],
                confirmed=True,
                environment={
                    "OPENAI_API_KEY": "test-key",
                    "NOTEWITNESS_OPENAI_MODEL": "test-model",
                },
                transport=OpenAIHTTPTransport(opener),
            )

    def test_incomplete_response_is_rejected(self) -> None:
        graph = remote_graph()
        response = completed_response({"suggestions": []})
        response["status"] = "incomplete"
        opener = FakeOpener(FakeResponse(json.dumps(response).encode("utf-8")))
        with self.assertRaisesRegex(OpenAIOutputError, "did not complete"):
            OpenAIRelationSuggester.suggest_authorized(
                graph=graph,
                event_ids=["event:instruction", "event:demonstration"],
                confirmed=True,
                environment={
                    "OPENAI_API_KEY": "test-key",
                    "NOTEWITNESS_OPENAI_MODEL": "test-model",
                },
                transport=OpenAIHTTPTransport(opener),
            )

    def test_refusal_is_rejected_without_parsing_provider_text(self) -> None:
        graph = remote_graph()
        response = completed_response({"suggestions": []})
        response["output"] = [
            {
                "type": "message",
                "content": [
                    {
                        "type": "refusal",
                        "refusal": "Provider-controlled refusal text.",
                    }
                ],
            }
        ]
        opener = FakeOpener(FakeResponse(json.dumps(response).encode("utf-8")))

        with self.assertRaisesRegex(OpenAIOutputError, "declined"):
            OpenAIRelationSuggester.suggest_authorized(
                graph=graph,
                event_ids=["event:instruction", "event:demonstration"],
                confirmed=True,
                environment={
                    "OPENAI_API_KEY": "test-key",
                    "NOTEWITNESS_OPENAI_MODEL": "test-model",
                },
                transport=OpenAIHTTPTransport(opener),
            )


if __name__ == "__main__":
    unittest.main()
