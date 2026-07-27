# Capability matrix

## Implemented scope

NoteWitness currently implements:

- private regular-file ingestion and bounded local `ffprobe` inspection;
- explicit local Whisper CLI ASR with an absolute checkpoint, raw/normalized artifacts, and
  provenance;
- explicit JSON CLI adapters for activity, anonymous diarization/overlap, note, pitch,
  instrument detection/diarization, and optional score-alignment hypotheses, with packaged local
  pyannote, Basic Pitch, and PANNs speech/music and instrument-activity bridges;
- deterministic, run-aware speech-to-anonymous-speaker alignment and source-aware private CSV/MIDI
  note export plus HTML/TXT/WebVTT transcript export with explicit rights and projection-loss gates;
- SQLite-backed resumable analysis jobs with leases, heartbeats, bounded continuation chunks,
  cancellation, checkpoints, raw replay, exact executable checks around every stage, and
  identity checks before resume; and
- a session-authenticated `127.0.0.1` graphical workbench for streaming media import, media range playback, durable
  exclusively owned local-model jobs, bounded cancellation/resume, crash-safe publication
  reconciliation, browser capture, reviewer onboarding, evidence and pedagogical-relation review,
  bookmarks, lesson/practice views, descriptive statistics, tuner, and metronome.

The evidence graph remains the boundary: raw model output, normalized hypotheses, accepted
annotations, and summaries are separate. A rerun cannot overwrite a human review record.

## Component map

```text
CLI
  +-- project / media_ingest -------- owner-private project and explicit local media
  +-- adapters/whisper_cli ---------- local ASR, checkpoint, raw artifact normalization
  +-- adapters/analysis_cli --------- bounded JSON activity/diarization/music/alignment adapter
  +-- bridges ----------------------- optional pyannote, Basic Pitch, PANNs, MT3 boundaries
  +-- transcription_runtime --------- ASR manifests, evidence, and export gates
  +-- speaker_alignment ------------ run-aware anonymous speaker links
  +-- pedagogical_digest ----------- conservative local assignment suggestions
  +-- transcript_export ------------ source-specific HTML/TXT/WebVTT projection
  +-- music_export ----------------- source-aware CSV and deterministic MIDI projection
  +-- resumable_analysis ------------ SQLite jobs, lease, checkpoint, raw replay
  +-- workbench_processing ---------- durable GUI queue, retry, cancellation, progress
  +-- workbench_local_executor ------ startup-approved local ASR/analysis composition
  +-- workbench_server/assets ------- loopback review, import/playback/capture, Web Audio
  +-- evidence ---------------------- compatibility façade and provenance validation
  +-- providers/openai_responses ---- separately gated remote text suggestions
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

## Excluded and unverified scope

No models, checkpoints, Whisper, pyannote, Basic Pitch, PANNs, FFmpeg/ffprobe, browser, or device
are bundled. The optional provider bridges do not download or select model weights.
An adapter being implemented does not mean a compatible engine is installed, licensed, available,
or accurate for the intended lesson/corpus. The prototype does not persist voice identity, assign
teacher/student identity automatically, grade performance, diagnose learners, or establish
pedagogical conclusions.

It makes no full empirical accuracy, fairness, or noScribe-equivalence claim. Those require an
authorized, stratified corpus, predeclared measures, retained failures, and a human-review
protocol. Runtime success is integration evidence, not corpus validation.

## Privacy, licenses, and remote boundary

The local workflows are offline by default. On macOS external local tools run under
`sandbox-exec` with network access denied and have bounded time, arguments, and output. This
subprocess contract does not itself establish a third-party binary's safety or licensing. Model
code, weights, external tools, media, and scores each need separate provenance and rights review.
The sandbox does not restrict filesystem reads or writes, so every approved executable and model
loader must be trusted with the invoking user's filesystem authority.

The optional OpenAI Responses feature is outside the local analysis path: it is text-only,
requires `remote_explicit`, source/evidence rights, and a per-call confirmation, uses `store:
false`, and returns a machine suggestion. It never uploads media automatically.

The executable Whisper export subset honors pause markers and timestamp visibility/interval.
ASR segments and anonymous speaker turns are linked by maximum positive temporal overlap while
preserving equal-overlap ties and isolating diarization reruns. This is deterministic integration,
not a word-speaker-error or identity-accuracy claim. Disfluency suppression and empirical noScribe
parity remain unclaimed; unsupported behavior is rejected or represented as a separate reviewable
evidence layer.

See [operator-guide.md](operator-guide.md) for commands and recovery actions.
