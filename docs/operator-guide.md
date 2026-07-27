# Operator guide

## Boundary

This is an executable local path, not an empirical validation study. It accepts explicit private
local media and external tools/models, then records reviewable automatic hypotheses. The checkout
bundles no models, Whisper, FFmpeg/ffprobe, analysis engine, browser, microphone, or media device.
Each must be installed, compatible, and licensed by the operator. Optional first-party provider
bridges are packaged for Basic Pitch, pyannote, and PANNs, but their Python runtimes and weights
are deliberately not bundled or downloaded.

On macOS, approved external tools run with network operations denied and with bounded arguments,
environment, time, output, resource use, and process-group cleanup. Executable identity is checked
around execution. Filesystem reads and writes are not restricted, so the operator must trust each
approved executable and model loader with the invoking user's filesystem authority.

Automatic output is never an accepted research claim by itself. It has no persistent identity
recognition, learner grading, diagnosis, or performance assessment. Human acceptance/revision is
a separate append-only record.

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

`ingest-media` prints JSON containing the assigned `source_id`. Replace
`SOURCE_ID_FROM_INGEST` in every later command with that exact value.

Whisper is explicit local ASR: named-model selection and downloads are not accepted. `ffprobe`
provides bounded descriptive metadata. ASR raw output and normalized time-bounded words/segments
are retained separately. The run records source, launcher, checkpoint, settings, runtime, and
license provenance. Local execution does not make a third-party executable safe or licensed.
Pause markers and timestamp visibility/interval are executable HTML/TXT export controls and are
recorded in the run manifest. WebVTT necessarily retains cue timestamps. The local Whisper adapter
accepts included disfluencies; it rejects suppression because the adapter cannot prove that the
underlying engine honored it.

## Analysis adapters and durable jobs

`analyze-local` invokes one supplied JSON-producing local analysis CLI. It validates bounded JSON
hypotheses for activity segmentation, anonymous diarization, note transcription, continuous pitch,
instrument detection, instrument-activity diarization, and optional score alignment. It does not
select, download, or endorse a model. The packaged `notewitness-provider-bridge` supplies
strict offline normalizers for Basic Pitch notes, pyannote speaker turns, and PANNs framewise
speech/music activity and instrument activity; see [provider-bridges.md](provider-bridges.md).

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

Anonymous diarization labels clusters; it does not identify people. `auto` is the default;
`exact` requires an explicit count from 1 to 10. `--detect-overlap` requests hypotheses, not proof
of quality. Completed ASR and diarization evidence is linked by deterministic maximum temporal
overlap. Equal-overlap ties remain multiple machine-suggested relations; no voiceprint, persistent
identity, or teacher/student identity is inferred. PANNs diarization creates anonymous
instrument-class activity tracks, not separated performers or distinct same-class instruments.
Use `--one-shot`, or default durable jobs. `--enqueue-only` records a job without processing it;
`--resume --job-id …` resumes a compatible job.

Durable jobs live in `runs/analysis-jobs.sqlite` and use bounded leases, active heartbeats,
checkpoints, and up to 64 continuation chunks per stage. On restart, completed raw chunks are
replayed from private artifacts, not rerun; raw output written just before a crash advances the
next checkpoint after validated replay. Resume refuses changed source, model, adapter, runtime,
score, or settings identities. The analyzer's captured byte and filesystem identity is part of
the durable fingerprint and is checked immediately before and after each stage; a replacement or
in-place mutation fails the job before raw output or graph evidence can be published.

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

Cancellation leaves a durable state, not a successful result. During an external analysis call the
runner polls the durable cancellation request every 250 ms, terminates the complete child process
group with bounded TERM/KILL cleanup, preserves valid checkpoints, and publishes no evidence from
the cancelled stage. Recover an expired lease only after the prior worker has stopped, and resume
paused work only with matching identities. A failed job is terminal: inspect its private raw failure
artifact, correct the deterministic cause, and start a new job. Failed ASR similarly keeps private
recovery status/artifacts and publishes no evidence after a failed stage.

Completed ASR and `--one-shot` analysis runs seal `publication.completed.json` before graph
integration. Publication is merged into the latest project transaction, so an unrelated bookmark,
review, or practice update made while a model runs is preserved. If integration itself fails, use
the run ID from `status.integration-failed.json` without rerunning the model:

```sh
PYTHONPATH=src python3 -m notewitness integrate-run \
  /path/to/private/project run:0123456789abcdef0123456789abcdef
```

The command verifies immutable artifact checksums plus source, rights, model, and run identity. It
then appends deterministic run-owned records or confirms that the exact records already exist;
repeating it never duplicates evidence. A changed source/rights record or a conflicting graph ID
is a hard failure requiring operator review.

## Graphical local workbench

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

The workbench binds only to `127.0.0.1`. It supplies checksum-verified byte-range playback of
ingested media, browser streaming import, durable background local-model jobs, browser
`MediaRecorder` capture, first-reviewer setup, review/revision, exact-time bookmarks, lesson
overview, practice-task state, descriptive statistics, private transcript/music export, and Web
Audio tuner/metronome controls. Job state is stored in the owner-private project and survives
reopening; a complete pass checkpoints transcription and analysis separately so retry does not
repeat a stage already recorded complete. One workbench
holds an owner-private project processing lock for its lifetime; a second instance fails closed
instead of recovering or competing with the live worker. Cancelled or failed partial passes show
their already-published evidence immediately and offer Resume for only the remaining stages.
Each job attempt has a deterministic private run identity. If the process stops after evidence
integration but before the SQLite step checkpoint, resume validates and reconciles that immutable
publication, then records the checkpoint without rerunning the model.

Private API, job, media, and mutation routes require a per-process session cookie. The server opens
a single-use launch URL to establish that cookie. With `--no-open-browser`, the command prints the
single-use URL to the protected terminal. Host, Origin, and CSRF checks remain separate controls.
The token does not protect against a malicious process already running with the same user and
filesystem authority.

If a project has no reviewer, the GUI creates one project-local restricted actor before enabling
review mutations. The reviewer and the attributed project actor must still be selected explicitly.
Capture is bounded to two hours and 512 MiB, records actor/time/name/container provenance atomically, and
checks the declared container signature before publication. This signature check is not a full
codec-decoding or media-forensics validation.

Automatic GUI processing is disabled unless `--runtime-config` names an absolute, owner-private
JSON file (mode `0600`) that explicitly approves every executable path, checkpoint/model path,
license, and analysis stage. Start from
[`docs/workbench-runtime.example.json`](workbench-runtime.example.json). The browser API never
accepts executable, model, score, or arbitrary filesystem paths. The server captures tool identity
at startup; executables, PATH-selected FFmpeg, and the Whisper checkpoint are identity-checked
around execution, and every local adapter still enforces network denial and bounded execution. A
config may contain either engine or both; the interface reports the missing capability rather
than pretending it is ready. Because Whisper locates FFmpeg by command name, the resolved approved
FFmpeg executable must have the exact basename `ffmpeg`; this prevents an unapproved sibling from
being selected through `PATH`.

Runtime configuration version 2 assigns one executable, model artifact, version, license, timeout,
and bounded parameter object to each analysis stage. This permits pyannote, Basic Pitch, and PANNs
to run in isolated operator-managed environments instead of pretending that one monolithic model
implements every modality. Model artifacts may be private files or private, symlink-free directory
trees; each complete identity is rechecked around execution.

The GUI offers `Complete lesson pass` only when speech transcription, speech/music activity,
anonymous speaker diarization, note transcription, and instrument diarization are all configured.
Partial passes stay available and list the modalities they do not provide.

The GUI workflow is: import or record a source, choose a configured local pass, monitor or cancel
the durable job, review speech/music activity, transcripts, notes, instruments, and pedagogical
relation suggestions, revise textual evidence with a recorded reason, and only then accept it as
human evidence. After local speech analysis, conservative exact-prefix rules may propose explicit
practice instructions as source-linked `assigned_for_practice` relations. They never infer learner
state, never become practice tasks automatically, and require both transcript review and separate
relation acceptance. The source selector controls playback, timeline scale, bookmarks, and job
targeting, including newly captured media that has no annotations yet.

The `Transcript export` panel writes one selected recording as private HTML, TXT, or WebVTT.
Accepted evidence is the default; including unreviewed machine suggestions is an explicit choice,
and every such line is visibly prefixed. Project-local speaker labels are retained. The preflight
requires rights authorization and acknowledgement that evidence-graph metadata is lost; WebVTT
also reports its pause/inline-timestamp projection losses. Existing files are never overwritten.

The `Music transcript` panel creates new, owner-private CSV or MIDI exports only after explicit
rights authorization and acknowledgement of projection losses. CSV retains source spans, review
state, track IDs, frequency, amplitude, velocity, and pitch-bend metadata when available. MIDI
retains separate named tracks and explicit velocities; timing quantization, evidence provenance,
provider amplitude, fractional pitch, pitch-bend omission, and overlapping-same-pitch merging are
reported before export. MIDI is limited to one selected recording so independent source clocks
cannot be combined accidentally. The same gate is available without a browser:

```sh
PYTHONPATH=src python3 -m notewitness export-music \
  /path/to/private/project --source-id SOURCE_ID_FROM_INGEST \
  --format csv --filename lesson-notes.csv \
  --authorize-local-export --acknowledge-export-losses
```

Capture and live audio require a compatible browser, permitted device, user gesture, and consent;
code support does not guarantee availability on every host. The server restricts filesystem
exposure and uses same-origin/CSRF checks for mutations.

## Review and research boundary

Create project-local review actors and accept only what a human has examined. Acceptance adds an
adjudication revision; it never overwrites the machine result. Optional HTML, TXT, and WebVTT
speech exports and CSV/MIDI note exports require local rights authorization and acknowledgement
of format losses. A note with an accepted successor is exported once as accepted evidence, not a
second time as its superseded machine suggestion.

Runtime success proves only that a supplied tool completed this bounded local invocation. Before
using automatic output beyond private exploration, evaluate it on an authorized, stratified corpus;
define error measures and review protocol; retain failures; and document model, version, rights,
and operating conditions. This repository makes no full accuracy, fairness, pedagogical
effectiveness, or noScribe-equivalence claim.

The optional OpenAI relation-suggestion path is separate, text-only, explicitly gated, and still
produces only a machine suggestion.
