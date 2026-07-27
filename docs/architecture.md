# Architecture

## Decision and evidence boundary

The executable spine is a Python 3.11+ standard-library application. Private project state and
evidence are validated in-process. Long-running local analysis uses SQLite for durable job state;
media and raw artifacts remain private project files. External ASR and MIR behavior stays behind
explicit local adapters, with code/model/score licenses and identities recorded separately.
On macOS the adapter runner denies network operations and bounds process resources, but it does not
restrict filesystem access by an approved executable.

```text
private source media / score
        |
        +--> explicit local Whisper or JSON analysis CLI --> raw artifact
        |                                                    |
        |                                             normalized hypothesis
        |                                                    |
        +----------------------> evidence graph <--- human acceptance/revision
                                       |
                  loopback workbench / rights-gated local export
```

`notewitness.evidence` is a thin public compatibility façade. Internals operate on payload
mappings and do not import that façade. Every evidence-bearing event targets source or score
evidence. Automatic records retain generator/model provenance and stay `machine_suggested` until
an append-only human adjudication revision accepts or supersedes them.

## Local execution paths

The ASR path accepts only an explicit local Whisper executable and absolute checkpoint; it does
not select named models or download weights. It retains raw CLI JSON separately from normalized,
time-bounded transcript hypotheses and records source, tool, model, settings, and runtime
identities.

The analysis path accepts one explicit JSON-producing local CLI. Typed stages cover activity,
anonymous diarization (including optional overlap and exact cluster counts from 1 to 10), notes,
continuous pitch, instruments, and optional score alignment. The adapter validates a bounded
contract but does not attest the engine's installation, license, model quality, or suitability.

`ResumableAnalysisCoordinator` persists jobs in project-local SQLite. A lease owns processing;
expired leases recover to a resumable state. Stage checkpoints and private raw JSON let a resumed
job replay completed output rather than rerun it. Resume checks source, adapter, runtime, model,
score, and settings identities and rejects drift. Cancellation leaves an explicit durable state.

## Local workbench

The standard-library HTTP server binds only to `127.0.0.1`. A single-use launch URL establishes a
per-process, host-only session cookie. Private API, job, media, and mutation routes require that
cookie; mutations also retain Host, Origin, and CSRF checks. This prevents access by local processes
that do not know the token, but not by a malicious process with the same user's filesystem
authority. Project actor IDs remain evidence attribution, not authenticated principals.

The server projects the evidence graph and serves only fixed assets, authenticated same-origin APIs,
and ingested media with HTTP Range support. It includes
review/revision, bookmarks, lesson overview, practice state, descriptive statistics, durable
startup-approved local processing jobs, browser `MediaRecorder` capture, and Web Audio
tuner/metronome controls. Browser capture/playback is
runtime-dependent: compatibility, device access, user gesture, and consent remain outside the
Python server's guarantee.

The GUI queue has one exclusive project owner at a time. Its atomic active-job transitions prevent
two HTTP requests from starting or resuming competing work. Transcription and analysis use
deterministic per-job attempt run identities; immutable completed publications are reconciled
before a recovered step can invoke a model again. This closes the crash window between evidence
graph integration and the GUI's SQLite step checkpoint.

## Validation boundary

The architecture is local-first and offline by default, but it bundles no external models or
engines. A completed invocation proves a bounded integration path, not recognition accuracy,
bias, grading validity, identity inference, or noScribe equivalence. Any research claim needs an
authorized, stratified corpus with defined measures, failure retention, and human review.

The optional OpenAI provider is separate from local analysis. It may receive only explicitly
selected text after project policy, rights, and per-call gates; it uses `store: false` and produces
machine-suggested relations only.
