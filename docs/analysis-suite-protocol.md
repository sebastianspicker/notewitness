# Analysis-suite JSON CLI protocol

This is the executable contract for `analyze-local`'s external analysis-suite adapter. It is a
strict local integration contract, not a bundled engine, model, license grant, or corpus-accuracy
claim. Every result is a machine hypothesis requiring human review.

## Invocation and isolation

The configured executable is run once per stage with this exact argument list:

```text
analysis-suite --request request.json
```

Its current working directory is a new owner-private temporary directory. `request.json` is UTF-8
JSON, mode `0600`; the directory is mode `0700`. The adapter supplies finite timeout, argument,
and output bounds and requests network denial. The executable must read the relative request file
and emit its complete response to stdout. Stderr is diagnostic only and not protocol output.

The input file has a 2 MiB limit, a maximum JSON depth of 32, finite numbers only, and bounded
strings/collections. Duplicate JSON object keys are rejected. The stdout response has the same
2 MiB maximum; at most 50,000 hypotheses are accepted.

## Schema version 1 request

The request object has exactly these keys:

```json
{
  "schema_version": 1,
  "stage": "activity_segmentation",
  "version": "ENGINE-VERSION",
  "generator_id": "generator:analysis-...",
  "model": {"source_id": "model:...", "path": "/absolute/model", "sha256": "...", "size_bytes": 1},
  "job_id": "job:...",
  "source_id": "source:...",
  "media": {
    "source_id": "source:...", "path": "/absolute/media", "sha256": "...", "size_bytes": 1
  },
  "score": null,
  "spans": [{"stream_id": "audio", "start_us": 0, "duration_us": 300000000}],
  "parameters": {},
  "continuation_token": null
}
```

`model`, `media`, and non-null `score` are runtime-owned identity objects with exactly
`source_id`, absolute non-symlink `path`, lowercase SHA-256 `sha256`, and positive `size_bytes`.
Media and scores are owner-private regular files. A model may instead be an owner-private,
symlink-free directory tree; its digest covers every relative path and file digest and
`size_bytes` is the total file-byte count. The adapter verifies those identities before and after
execution. These paths are deliberately
included so the isolated engine can read the configured inputs. They are not arbitrary operator
parameters: `parameters` rejects path-like keys, media-like keys, absolute paths, and `~` paths.

`score` is either `null` or the same identity object. Score alignment needs an explicit score path,
ID, and license at the CLI boundary. `spans` contain `stream_id`, non-negative `start_us`, and
positive `duration_us`. A response span must remain within a requested span.

Supported `stage` values are `activity_segmentation`, `anonymous_diarization`,
`note_transcription`, `continuous_pitch`, `instrument_detection`,
`instrument_diarization`, and `score_alignment`.
`version` and `generator_id` are bounded non-empty strings supplied by the host.

## Stage parameters

Only the host constructs stage parameters. For anonymous diarization it sends:

```json
{"detect_overlap": false, "diarization_mode": "auto", "exact_speaker_count": null}
```

`diarization_mode` is `off`, `auto`, or `exact`. With `exact`, `exact_speaker_count` must be an
integer from 1 through 10; otherwise it is null. `detect_overlap` is a boolean request for overlap
hypotheses, not an identity or accuracy claim. Score alignment receives a `score_id` parameter.
Other supported stages receive an empty object from the current CLI.

Workbench runtime configuration v2 may approve bounded, JSON-only stage parameters at server
startup. Keys containing `path` or `media`, absolute/tilde strings, non-finite numbers, and deep or
oversized collections are rejected. The maintained PANNs bridge requires `window_us`, `hop_us`,
`activation_threshold`, `merge_gap_us`, and a non-empty case-sensitive `instrument_labels`
allowlist drawn from the pinned checkpoint taxonomy. These exact effective values are retained in
the normalized artifact, run manifest, and generator provenance. The allowlist prevents general
AudioSet classes from being represented as instrument evidence; temporal window and hop values
must be calibrated for the exact local SoundEventDetection checkpoint and provider revision.
For PANNs activity segmentation, the same first four parameters are accompanied by exact
case-sensitive `speech_label` and `music_label` taxonomy names; simultaneous activation is
represented as `speech_over_music`, while below-threshold frames do not imply silence.

`continuation_token` is null or a bounded token. It supports an `incomplete` result; a non-null
output continuation is valid only when output state is `incomplete`.

## Exact stdout response

The executable must emit one JSON object with exactly these keys:

```json
{
  "state": "ready",
  "hypotheses": [],
  "diagnostics": [],
  "continuation_token": null
}
```

`state` is one of `ready`, `unknown`, `not_detected`, `not_applicable`, `not_alignable`,
`unsupported`, `uncertain`, `incomplete`, `cancelled`, or `failed`. Diagnostics are unique bounded
strings. Each hypothesis has exactly `hypothesis_id`, `span`, `state`, and `confidence`, plus the
following stage fields:

- `activity_segmentation`: `kind`, nullable and one of `speech`, `music`,
  `sung_or_hummed`, `speech_over_music`, `silence`, or `other_sound`.
- `anonymous_diarization`: nullable `anonymous_cluster_id`, required for `ready`.
- `note_transcription`: required `midi_pitch` (0–127 or null) and `frequency_hz` (positive or
  null), plus optional `source_track_id`, `amplitude` (0–1), `velocity` (0–127),
  `pitch_bend_values`, and `pitch_bend_unit`. Non-empty pitch bends require an explicit unit;
  provider amplitude is not automatically reinterpreted as calibrated confidence.
- `continuous_pitch`: `frequency_hz` (positive or null).
- `instrument_detection`: nullable `instrument_label`, required for `ready`, plus optional
  `anonymous_instrument_track_id`.
- `instrument_diarization`: `instrument_label` and a run-local
  `anonymous_instrument_track_id`, both required for `ready`. The track ID is not a performer
  identity or a cross-project voice/instrument print.
- `score_alignment`: `outcome`, `score_id`, `score_position`, and `source_hypothesis_ids`.

`confidence` is null or a finite number in the unit interval. A ready note needs MIDI pitch or
frequency; a ready pitch point needs frequency. Alignment `outcome` is `aligned`, `unknown`,
`not_detected`, `not_applicable`, or `not_alignable`; `aligned` requires a score ID and position.

## Failure, replay, and provenance

The durable runner stores raw stdout privately for every completed stage. If the process fails or
stdout is invalid, bounded stdout is still retained for private recovery, but no invalid result is
published. Resuming revalidates raw UTF-8 JSON through the same parser and replays completed stages
without invoking the executable. It refuses changed source, model, adapter, runtime, score, or
settings identities. The adapter fingerprint includes the executable's captured byte and
filesystem identity. That identity is checked immediately before and after each execution, and a
changed executable cannot publish raw output or graph evidence. Cancellation is a durable job
state, not an accepted analysis result.

The caller must declare adapter, model, and score licenses at the CLI boundary. Neither that
provenance nor successful execution proves external-tool safety, rights to a score/model, model
availability, or accuracy on a lesson or corpus.
