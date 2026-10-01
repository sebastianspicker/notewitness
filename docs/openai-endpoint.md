# Optional OpenAI endpoint

NoteWitness is offline by default. This page documents the one optional remote
path: structured relation suggestions over text you select explicitly. It is
separate from local analysis and never uploads media automatically.

## Purpose

The remote provider suggests structured relations over selected event excerpts.
It does not replace local transcription, note detection, score alignment, or
human interpretation.

## Data boundary

- Default project policy: `offline`.
- The only allowed OpenAI policy is `remote_explicit`.
- `download_models_only` cannot call OpenAI.
- A CLI caller must also pass `--allow-remote`.
- The request sends only request-local aliases (`e0`, `e1`, ...) and the selected
  text bodies. Graph IDs and event types are mapped back locally.
- It adds no actor records, file names, project metadata, rights records, source
  media, or unselected graph data.
- Every selected event must be evidence-scoped with at least one source target.
  The event and every targeted source must explicitly permit remote processing
  in their rights records.
- Requests set `store: false`; this is a remote request nonetheless.
- Request bodies and authorization headers are never logged.
- The application cannot reliably remove names or other identifying details
  embedded in selected text. Inspect the local preview and minimize or
  pseudonymize excerpts before consenting.

Preview the minimized projection without reading credentials or opening the
network:

```sh
PYTHONPATH=src python3 -m notewitness preview-relations \
  /path/to/project.json --event event:instruction
```

The default preview shows aliases, local ID mappings, character counts, rights
status, and the request hash. Add `--include-text` only on a protected terminal
to inspect the exact text projection.

## Configuration

| Variable | Required | Meaning |
|---|---:|---|
| `OPENAI_API_KEY` | yes | Bearer credential; diagnostics report presence only. |
| `NOTEWITNESS_OPENAI_MODEL` | yes | Explicitly evaluated model or snapshot; no default. |

The credential is sent only to `https://api.openai.com/v1/responses`. Custom and
OpenAI-compatible endpoints are deferred until they have a separate
authentication and host-trust design.

## Current API contract

The adapter posts directly to the fixed
`https://api.openai.com/v1/responses` URL. The runtime sends the complete strict
[`SUGGESTION_SCHEMA`](../src/notewitness/lessons/openai_responses.py) shown
below.

```json
{
  "model": "configured-model",
  "instructions": "versioned application instructions",
  "input": "minimized JSON containing selected event excerpts",
  "max_output_tokens": 1024,
  "store": false,
  "text": {
    "format": {
      "type": "json_schema",
      "name": "notewitness_relation_suggestions",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": {
          "suggestions": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "relation_type": {
                  "type": "string",
                  "enum": [
                    "attempts",
                    "contrasts_with",
                    "demonstrates",
                    "feedback_on",
                    "refers_to",
                    "repeats",
                    "revises"
                  ]
                },
                "arguments": {
                  "type": "array",
                  "items": {
                    "type": "object",
                    "properties": {
                      "role": {
                        "type": "string",
                        "enum": [
                          "attempt",
                          "earlier_attempt",
                          "example",
                          "feedback",
                          "first_example",
                          "instruction",
                          "referent",
                          "repeat",
                          "revision",
                          "second_example",
                          "task",
                          "utterance"
                        ]
                      },
                      "event_ref": {"type": "string"}
                    },
                    "required": ["role", "event_ref"],
                    "additionalProperties": false
                  }
                },
                "rationale": {"type": "string"}
              },
              "required": ["relation_type", "arguments", "rationale"],
              "additionalProperties": false
            }
          }
        },
        "required": ["suggestions"],
        "additionalProperties": false
      }
    }
  }
}
```

The raw Responses API output can contain reasoning or tool items before or
between message items. The parser therefore collects every `output_text` content
part and fails if the response is incomplete, contains a `refusal`, or completes
without text. Refusal text is neither parsed nor persisted.

Provider output is bounded and normalized. Relation arguments must use fixed,
relation-specific semantic roles; response IDs use the expected `resp_...`
shape; and only known numeric usage fields are retained. The CLI omits provider
response and model identifiers and model-written rationales, because any
provider-controlled string may echo selected source text. The Python result
object keeps them in memory for a future protected review workflow. Results are
never persisted or promoted to a human review state automatically.

The transport rejects redirects, automatic retries, and environment-configured
HTTP proxies. It caps request and response bytes and uses a 15-second
socket-operation timeout. That timeout is not a guaranteed total wall-clock
deadline, because standard-library DNS and a trickling peer can take longer.
Application policy is also not an operating-system firewall; deployments that
need a hard offline guarantee must add process- or host-level egress controls.

Official references:

- [Migrate to Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Text generation](https://developers.openai.com/api/docs/guides/text)
