# Capability matrix

This page is the canonical list of what NoteWitness does today and what it
leaves out. The [README](../README.md) summarizes it, and
`notewitness capabilities` prints the same scope from the installed package.

## Implemented

- Private regular-file ingestion and bounded local `ffprobe` inspection.
- Explicit local Whisper CLI ASR with an absolute checkpoint, raw and normalized
  artifacts, and provenance.
- Explicit JSON CLI adapters for activity, anonymous diarization and overlap,
  note, pitch, instrument detection and diarization, and optional score
  alignment, with packaged local pyannote, Basic Pitch, and PANNs speech/music
  and instrument-activity bridges.
- Deterministic, run-aware speech-to-anonymous-speaker alignment, plus
  source-aware private CSV/MIDI note export and HTML/TXT/WebVTT transcript
  export behind explicit rights and projection-loss gates.
- SQLite-backed resumable analysis jobs with leases, heartbeats, bounded
  continuation chunks, cancellation, checkpoints, raw replay, exact executable
  checks around every stage, and identity checks before resume.
- A session-authenticated `127.0.0.1` workbench for streaming media import,
  media range playback, durable exclusively owned local-model jobs, bounded
  cancellation and resume, crash-safe publication reconciliation, browser
  capture, reviewer onboarding, evidence and pedagogical-relation review,
  bookmarks, lesson and practice views, descriptive statistics, tuner, and
  metronome.

The evidence graph is the boundary: raw model output, normalized hypotheses,
accepted annotations, and summaries stay separate, and a rerun cannot overwrite
a human review record.

## Where the code lives

```text
interfaces/CLI and bridges
  +-- projects ---------------------- owner-private project, artifacts, and media
  +-- analysis/transcription ------- bounded local Whisper ASR and transcript evidence
  +-- analysis/suite --------------- analysis-suite provider, durable jobs, checkpoints, raw replay
  +-- analysis/runs ---------------- run workspace, sealed publication, provenance-linked integration
  +-- lessons ----------------------- review, lesson projections, and guarded exports
  +-- workbench --------------------- loopback HTTP, durable GUI queue, and browser UI
  +-- core -------------------------- pure evidence and domain contracts
```

## Capability status

| Capability | Implemented code path | Runtime or validation boundary |
|---|---|---|
| Local media import | Regular file, checksum, private storage | Operator rights/storage |
| Speech suggestion | Supplied Whisper checkpoint | Tool/model and corpus evaluation |
| Speech/music activity | PANNs framewise speech/music bridge | No silence/humming inference; corpus evaluation required |
| Speaker diarization | pyannote bridge; overlap-preserving anonymous turns | No persistent identity; DER/JER evaluation required |
| Note transcription | Basic Pitch bridge; timing, amplitude, velocity/bend fields | Basic Pitch documents a one-instrument preference; onset/offset/drift evaluation required |
| Instrument diarization | PANNs framewise class-activity tracks | Not source separation, performer identity, or same-class instance separation |
| Analysis hypotheses | Per-stage JSON CLI, model, parameters, and licenses | Compatible/evaluated tools and models |
| Overlap / exact clusters | Overlap; anonymous count 1–10 | Not identity or accuracy proof |
| Cross-modal transcript | Source-time timeline and run-aware speech/speaker links | Machine relations require human review |
| Local lesson digest | Conservative explicit-instruction rules and relation review | No narrative summary or learner-state inference |
| Transcript export | Source-specific HTML/TXT/WebVTT with evidence-layer choice | Explicit rights/loss acknowledgement; machine text is visibly marked |
| Symbolic music export | Source-aware CSV; named-track deterministic MIDI | Explicit rights/loss acknowledgement; MIDI is lossy |
| Score alignment | Explicit score path, ID, and license | Score rights and corpus validation |
| Durable analysis | SQLite lease/heartbeat, continuation, recovery, replay | Matching persisted identities |
| Workbench | Session-authenticated loopback UI, import, verified Range, exclusive durable processing, partial-run resume, bounded capture, explicit review | Browser/device/host support; no defense against a malicious same-user process |
| Tuner/metronome | Web Audio and deterministic calculations | Browser/host audio availability |
| Human acceptance | Append-only acceptance and revision | Qualified human review |

## Out of scope and unverified

The repository bundles no models, checkpoints, Whisper, pyannote, Basic Pitch,
PANNs, FFmpeg/ffprobe, browser, or device. The provider bridges do not download
or select model weights. An adapter existing does not mean a compatible engine
is installed, licensed, available, or accurate for your lesson or corpus. The
prototype does not persist voice identity, assign teacher and student identity
automatically, grade performance, diagnose learners, or reach pedagogical
conclusions.

It makes no full empirical accuracy, fairness, or noScribe-equivalence claim.
Those need an authorized, stratified corpus, predeclared measures, retained
failures, and a human-review protocol. Passing at runtime is integration
evidence, not corpus validation.

## Privacy, licenses, and the remote boundary

Local workflows are offline by default. On macOS, external local tools run under
`sandbox-exec` with network access denied and bounded time, arguments, and
output. This subprocess contract does not make a third-party binary safe or
licensed. Model code, weights, external tools, media, and scores each need
separate provenance and rights review. The sandbox does not restrict filesystem
reads or writes, so every approved executable and model loader must be trusted
with the invoking user's filesystem authority.

The optional OpenAI Responses feature sits outside the local analysis path. It
is text-only, requires `remote_explicit`, source and evidence rights, and a
per-call confirmation, uses `store: false`, and returns a machine suggestion. It
never uploads media automatically.

The executable Whisper export subset honors pause markers and timestamp
visibility and interval. ASR segments and anonymous speaker turns link by
maximum positive temporal overlap while preserving equal-overlap ties and
isolating diarization reruns. This is deterministic integration, not a
word-speaker-error or identity-accuracy claim. Disfluency suppression and
empirical noScribe parity remain unclaimed; unsupported behavior is rejected or
kept as a separate reviewable evidence layer.

See [operator-guide.md](operator-guide.md) for commands and recovery actions.
