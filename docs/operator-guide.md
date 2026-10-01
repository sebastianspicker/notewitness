# Operator guide

This guide walks through the local NoteWitness workflow: create a private
project, ingest media, run approved local tools, review the results, and export
what the recorded rights allow. Every model-generated result stays a machine
hypothesis until a person accepts it.

## Limits at a glance

- **Nothing is bundled.** You install, license, and trust each model,
  checkpoint, and executable, including Whisper, FFmpeg/ffprobe, pyannote,
  Basic Pitch, and PANNs.
- **macOS runs approved tools under a network-denying sandbox** with bounded
  arguments, environment, time, output, resource use, and process-group
  cleanup. Filesystem access is not restricted, so an approved tool runs with
  your filesystem authority.
- **Automatic output is never an accepted research claim.** Human acceptance
  and revision is a separate, append-only record.
- **Runtime success is not accuracy.** It proves only that a supplied tool
  completed one bounded local invocation.

## Core workflow

```sh
PYTHONPATH=src python3 -m notewitness init /path/to/private/project \
  --name "Lesson study"
PYTHONPATH=src python3 -m notewitness ingest-media \
  /path/to/private/project /path/to/lesson.m4a --create-restricted-rights \
  --ffprobe-path /absolute/path/to/ffprobe
PYTHONPATH=src python3 -m notewitness runtime-doctor \
  --ffprobe-path /absolute/path/to/ffprobe --whisper-path /absolute/path/to/whisper \
  --ffmpeg-path /absolute/path/to/ffmpeg \
  --model-checkpoint /absolute/path/to/whisper-model.bin \
  --model-license "MODEL-LICENSE" --adapter-license "ADAPTER-LICENSE" \
  --ffmpeg-license "FFMPEG-LICENSE"
PYTHONPATH=src python3 -m notewitness transcribe-local \
  /path/to/private/project SOURCE_ID_FROM_INGEST \
  --model-checkpoint /absolute/path/to/whisper-model.bin \
  --model-license "MODEL-LICENSE" --adapter-license "ADAPTER-LICENSE" \
  --ffmpeg-license "FFMPEG-LICENSE" --ffprobe-path /absolute/path/to/ffprobe \
  --whisper-path /absolute/path/to/whisper --ffmpeg-path /absolute/path/to/ffmpeg \
  --pause-ms 2000 --visible-timestamps --timestamp-interval-ms 60000 \
  --format html --authorize-local-export --acknowledge-export-losses
```

`ingest-media` prints JSON containing the assigned `source_id`. Use that exact
value in place of `SOURCE_ID_FROM_INGEST` in every later command.

## Transcription

Whisper is explicit local ASR: you name the checkpoint, and the adapter never
selects or downloads a model. `ffprobe` supplies bounded descriptive metadata.
Raw ASR output and normalized time-bounded words and segments are retained
separately, and the run records source, launcher, checkpoint, settings, runtime,
and license provenance. Running a third-party executable locally does not make
it safe or licensed.

Pause markers and timestamp visibility and interval are executable HTML/TXT
export controls, and the run manifest records them. WebVTT keeps cue timestamps
by design. The Whisper adapter passes through disfluencies; it rejects
suppression because it cannot prove the underlying engine honored it.

## Analysis adapters and durable jobs

`analyze-local` invokes one supplied JSON-producing local analysis CLI. It
validates bounded JSON hypotheses for activity segmentation, anonymous
diarization, note transcription, continuous pitch, instrument detection,
instrument-activity diarization, and optional score alignment. It never selects,
downloads, or endorses a model.

The packaged `notewitness-provider-bridge` supplies strict offline normalizers
for Basic Pitch notes, pyannote speaker turns, and PANNs framewise speech/music
activity and instrument activity. See [provider-bridges.md](provider-bridges.md).

```sh
PYTHONPATH=src python3 -m notewitness analyze-local \
  /path/to/private/project SOURCE_ID_FROM_INGEST --analysis-path /absolute/path/to/analysis-suite \
  --adapter-version "ENGINE-VERSION" --adapter-license "ADAPTER-LICENSE" \
  --model-path /absolute/path/to/analysis-model --model-license "MODEL-LICENSE" \
  --start-us 0 --duration-us 300000000 --detect-overlap --diarization-mode exact \
  --exact-speaker-count 2 --stage activity_segmentation \
  --stage anonymous_diarization --stage note_transcription --stage continuous_pitch \
  --stage instrument_detection --stage instrument_diarization \
  --enqueue-only --job-id job:lesson-001
PYTHONPATH=src python3 -m notewitness analyze-local \
  /path/to/private/project SOURCE_ID_FROM_INGEST --analysis-path /absolute/path/to/analysis-suite \
  --adapter-version "ENGINE-VERSION" --adapter-license "ADAPTER-LICENSE" \
  --model-path /absolute/path/to/analysis-model --model-license "MODEL-LICENSE" \
  --duration-us 300000000 --stage score_alignment \
  --score-path /absolute/path/to/score.musicxml --score-id score:lesson \
  --score-license "SCORE-LICENSE" --one-shot
```

Anonymous diarization labels clusters; it does not identify people. The default
mode is `auto`; `exact` requires an explicit count from 1 to 10.
`--detect-overlap` requests hypotheses, not proof of quality. Completed ASR and
diarization evidence link by deterministic maximum temporal overlap, and
equal-overlap ties stay as multiple machine-suggested relations. NoteWitness
never infers a voiceprint, a persistent identity, or teacher and student
identity. PANNs diarization creates anonymous instrument-class activity tracks,
not separated performers or distinct same-class instruments.

Use `--one-shot` for a single pass, or the default durable jobs.
`--enqueue-only` records a job without processing it; `--resume --job-id …`
resumes a compatible job.

### Job lifecycle

Durable jobs live in `runs/analysis-jobs.sqlite` and use bounded leases, active
heartbeats, checkpoints, and up to 64 continuation chunks per stage. On restart,
the runner replays completed raw chunks from private artifacts instead of
rerunning them. Raw output written just before a crash advances the next
checkpoint after a validated replay. Resume refuses changed source, model,
adapter, runtime, score, or settings identities. The runtime identity hashes
the NoteWitness analysis-suite coordinator, checkpoint, adapter, protocol,
identity, and evidence modules and the core analysis validation module
(`notewitness.core.analysis.analysis`), so an upgrade that changes any of them
refuses to resume jobs enqueued before it. The analyzer's captured byte
and filesystem identity is part of the durable fingerprint and is checked
immediately before and after each stage; a replacement or in-place mutation
fails the job before it can publish raw output or graph evidence.

```sh
PYTHONPATH=src python3 -m notewitness analysis-job /path/to/private/project
PYTHONPATH=src python3 -m notewitness analysis-job \
  /path/to/private/project job:lesson-001 --cancel
PYTHONPATH=src python3 -m notewitness analysis-job \
  /path/to/private/project --recover-stale
PYTHONPATH=src python3 -m notewitness analyze-local \
  /path/to/private/project SOURCE_ID_FROM_INGEST --analysis-path /absolute/path/to/analysis-suite \
  --adapter-version "ENGINE-VERSION" --adapter-license "ADAPTER-LICENSE" \
  --model-path /absolute/path/to/analysis-model --model-license "MODEL-LICENSE" \
  --duration-us 300000000 --job-id job:lesson-001 --resume
```

Cancellation leaves a durable state, not a successful result. During an external
analysis call the runner polls the durable cancellation request every 250 ms,
terminates the complete child process group with bounded TERM/KILL cleanup,
preserves valid checkpoints, and publishes no evidence from the cancelled stage.
Recover an expired lease only after the prior worker has stopped, and resume
paused work only with matching identities. A failed job is terminal: inspect its
private raw failure artifact, fix the deterministic cause, and start a new job.
Failed ASR keeps private recovery status and artifacts, and publishes no
evidence after a failed stage.

### Run integration

Completed ASR and `--one-shot` analysis runs seal `publication.completed.json`
before graph integration. Integration merges into the latest project
transaction, so an unrelated bookmark, review, or practice update made while a
model runs survives. If integration itself fails, use the run ID from
`status.integration-failed.json` without rerunning the model:

```sh
PYTHONPATH=src python3 -m notewitness integrate-run \
  /path/to/private/project run:0123456789abcdef0123456789abcdef
```

The command verifies immutable artifact checksums plus source, rights, model,
and run identity. It then appends deterministic run-owned records, or confirms
that the exact records already exist. Repeating it never duplicates evidence. A
changed source or rights record, or a conflicting graph ID, is a hard failure
that needs operator review.

## Graphical workbench

```sh
PYTHONPATH=src python3 -m notewitness workbench /path/to/private/project
PYTHONPATH=src python3 -m notewitness workbench \
  /path/to/private/project --port 8765 --no-open-browser
cp docs/workbench-runtime.example.json /absolute/path/to/workbench-runtime.json
chmod 600 /absolute/path/to/workbench-runtime.json
PYTHONPATH=src python3 -m notewitness workbench \
  /path/to/private/project \
  --runtime-config /absolute/path/to/workbench-runtime.json
```

The workbench binds only to `127.0.0.1` and provides checksum-verified
byte-range playback, browser streaming import, durable background local-model
jobs, browser `MediaRecorder` capture, first-reviewer setup, review and
revision, exact-time bookmarks, lesson overview, practice-task state,
descriptive statistics, private transcript and music export, and Web Audio
tuner and metronome controls.

Job state lives in the owner-private project and survives reopening. A complete
pass checkpoints transcription and analysis separately, so a retry does not
repeat a stage already recorded complete. One workbench holds an owner-private
project processing lock for its lifetime; a second instance fails closed instead
of recovering or competing with the live worker. Cancelled or failed partial
passes show their already-published evidence immediately and offer Resume for
only the remaining stages. Each job attempt has a deterministic private run
identity. If the process stops after evidence integration but before the SQLite
step checkpoint, resume validates and reconciles that immutable publication,
then records the checkpoint without rerunning the model.

### Workbench access

Private API, job, media, and mutation routes require a per-process session
cookie. The server opens a single-use launch URL to establish that cookie. With
`--no-open-browser`, the command prints the single-use URL to the protected
terminal. Host, Origin, and CSRF checks remain separate controls. The token does
not protect against a malicious process already running with the same user and
filesystem authority.

If a project has no reviewer, the GUI creates one project-local restricted actor
before enabling review mutations. You still select the reviewer and the
attributed project actor explicitly. Capture is bounded to two hours and
512 MiB, records actor, time, name, and container provenance atomically, and
checks the declared container signature before publication. That signature check
is not full codec-decoding or media-forensics validation.

### Runtime configuration

Automatic GUI processing stays disabled unless `--runtime-config` names an
absolute, owner-private JSON file (mode `0600`) that explicitly approves every
executable path, checkpoint or model path, license, and analysis stage. Start
from [`docs/workbench-runtime.example.json`](workbench-runtime.example.json).
The browser API never accepts executable, model, score, or arbitrary filesystem
paths. The server captures tool identity at startup; executables, PATH-selected
FFmpeg, and the Whisper checkpoint are identity-checked around execution, and
every local adapter still enforces network denial and bounded execution. A
config may contain either engine or both; the interface reports the missing
capability instead of pretending it is ready. Whisper locates FFmpeg by command
name, so the resolved approved FFmpeg executable must have the exact basename
`ffmpeg`; this prevents an unapproved sibling from being selected through
`PATH`.

Runtime configuration version 2 assigns one executable, model artifact, version,
license, timeout, and bounded parameter object to each analysis stage. That lets
pyannote, Basic Pitch, and PANNs run in isolated operator-managed environments
instead of pretending one monolithic model implements every modality. Model
artifacts may be private files or private, symlink-free directory trees; the
server rechecks each complete identity around execution.

The GUI offers `Complete lesson pass` only when speech transcription,
speech/music activity, anonymous speaker diarization, note transcription, and
instrument diarization are all configured. Partial passes stay available and
list the modalities they do not provide.

The GUI workflow is: import or record a source, choose a configured local pass,
monitor or cancel the durable job, review speech/music activity, transcripts,
notes, instruments, and pedagogical relation suggestions, then revise textual
evidence with a recorded reason before accepting it as human evidence. After
local speech analysis, conservative exact-prefix rules may propose explicit
practice instructions as source-linked `assigned_for_practice` relations. Those
rules never infer learner state, never create practice tasks automatically, and
require both transcript review and separate relation acceptance. The source
selector controls playback, timeline scale, bookmarks, and job targeting,
including newly captured media that has no annotations yet.

### Transcript export

The `Transcript export` panel writes one selected recording as private HTML,
TXT, or WebVTT. Accepted evidence is the default; including unreviewed machine
suggestions is an explicit choice, and every such line is visibly prefixed.
Project-local speaker labels are retained. The preflight requires rights
authorization and acknowledgement that evidence-graph metadata is lost; WebVTT
also reports its pause and inline-timestamp projection losses. Existing files
are never overwritten.

### Music export

The `Music transcript` panel creates new, owner-private CSV or MIDI exports only
after explicit rights authorization and acknowledgement of projection losses.
CSV retains source spans, review state, track IDs, frequency, amplitude,
velocity, and pitch-bend metadata when available. MIDI retains separate named
tracks and explicit velocities; before export it reports timing quantization,
evidence provenance, provider amplitude, fractional pitch, pitch-bend omission,
and overlapping-same-pitch merging. MIDI is limited to one selected recording so
independent source clocks cannot be combined accidentally. The same gate is
available without a browser:

```sh
PYTHONPATH=src python3 -m notewitness export-music \
  /path/to/private/project --source-id SOURCE_ID_FROM_INGEST \
  --format csv --filename lesson-notes.csv \
  --authorize-local-export --acknowledge-export-losses
```

Capture and live audio need a compatible browser, a permitted device, a user
gesture, and consent. Code support does not guarantee availability on every
host. The server restricts filesystem exposure and uses same-origin and CSRF
checks for mutations.

## Review and research boundary

Create project-local review actors and accept only what a human has examined.
Acceptance adds an adjudication revision; it never overwrites the machine
result. HTML, TXT, and WebVTT speech exports and CSV/MIDI note exports require
local rights authorization and acknowledgement of format losses. A note with an
accepted successor exports once as accepted evidence, not a second time as its
superseded machine suggestion.

Before using automatic output beyond private exploration, evaluate it on an
authorized, stratified corpus. Define error measures and a review protocol,
retain failures, and document model, version, rights, and operating conditions.
This repository makes no full accuracy, fairness, pedagogical effectiveness, or
noScribe-equivalence claim.

The optional OpenAI relation-suggestion path is separate, text-only, explicitly
gated, and still produces only a machine suggestion.
