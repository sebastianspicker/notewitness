# Local provider bridges

`src/notewitness/interfaces/bridges/` holds optional executable adapters for the
strict [analysis-suite JSON v1 protocol](analysis-suite-protocol.md). They are
not installed Python dependencies, and they never choose, download, cache, or
name a model. The host still owns the private media and model identities, the
model license, the timeout, and network isolation.

Installing this repository into an operator-managed provider environment creates
the executable `notewitness-provider-bridge`. The package does not install Basic
Pitch, pyannote, PANNs, or their models; you pin those separately and record
their licenses. A separate research-only executable,
`notewitness-mt3-events-bridge`, normalizes already-decoded MT3 events. Each
executable accepts one `--request` argument from an owner-private working
directory:

```text
notewitness-provider-bridge --request request.json
notewitness-mt3-events-bridge --request request.json
```

The bridge validates the complete v1 request, supports only its documented
stage, and emits one bounded v1 stdout object. If an optional runtime is missing
or a local artifact is malformed, it writes a bounded error to stderr and exits
2. It never emits a partial result and never makes a network request.

Model artifacts may be owner-private regular files or an owner-private,
symlink-free directory tree (needed by local pyannote Community-1 clones). The
host hashes every relative path and file byte before startup approval and again
around execution. Every directory and file must deny group and other access, and
the bridge accepts no hidden cache, hub name, URL, or mutable symlink as model
identity.

## Basic Pitch

`basic_pitch_bridge.py` supports `note_transcription` only. It passes the
host-supplied `model.path` unchanged to the locally installed
`basic_pitch.inference.predict` API and normalizes Basic Pitch note events into
MIDI note hypotheses. The official event tuple's amplitude is emitted as
`amplitude`, never miscast as confidence, and pitch bends are emitted as finite
`pitch_bend_values` with `pitch_bend_unit` set to the explicit provider value
`basic-pitch:semitone-offset`. No velocity is synthesized, because the event
tuple does not document one. The provider stays unavailable until you install its
runtime locally.

## pyannote

`pyannote_bridge.py` supports `anonymous_diarization` only. It passes the
host-supplied local pipeline artifact to `Pipeline.from_pretrained` and maps the
resulting annotation turns without merging them, so overlapping speakers remain
overlapping hypotheses. For Community-1 output it selects `speaker_diarization`
and never `exclusive_speaker_diarization`. The host links speech to these
reviewable turns by maximum positive temporal overlap and preserves
equal-overlap ties instead of hiding ambiguity. `exact` mode passes the
validated count as `num_speakers`; `auto` passes none; `off` emits an empty
disabled diagnostic without provider inference. Provider labels never leave this
boundary: clusters are renamed deterministically by first start time, then
original label, as `speaker-01`, `speaker-02`, and so on.

## MT3 decoded events

`mt3_decoded_events_bridge.py` avoids guessing at a direct MT3 Python API. Its
`model.path` must instead identify JSON output produced by an approved local MT3
decoded-event executable. The artifact has one `events` array; each event
supplies `start_s`, `end_s`, optional `confidence`, and the fields needed for
the selected stage:

```json
{"events":[{"start_s":0.0,"end_s":0.5,"midi_pitch":60,
"instrument_label":"piano","track":"part-a","confidence":0.8}]}
```

The bridge supports `note_transcription`, `instrument_detection`, and
`instrument_diarization`. It preserves overlaps and maps decoded instrument
tracks in first-seen event order to `track-01`, `track-02`; speaker diarization
stays exclusively with the pyannote bridge. Note events preserve their supplied
local `track` as `source_track_id` when present. The host must create and
provenance-record the decoded artifact; this bridge neither executes a model nor
makes an accuracy claim.

## PANNs speech/music activity

`panns_activity_bridge.py` supports `activity_segmentation` with the same
explicit local PANNs SoundEventDetection boundary described below. It requires
`window_us`, `hop_us`, `activation_threshold`, `merge_gap_us`, and exact,
case-sensitive `speech_label` and `music_label` names from the pinned checkpoint
taxonomy. For this packaged `SoundEventDetection` wrapper, `window_us` and
`hop_us` must both be `10000`, because upstream emits one framewise bin per 320
samples at 32 kHz. A frame above threshold for both labels becomes
`speech_over_music`; otherwise it becomes `speech` or `music`. Frames below both
thresholds never become invented silence evidence. Adjacent windows of the same
kind may merge only within the configured gap.

This is an AudioSet-class activity hypothesis, not voice activity ground truth,
source separation, or proof that every musical and speech condition was found.
Record the exact checkpoint, revision, and threshold in the research protocol
and provenance record.

## PANNs framewise instrument activity

`panns_instrument_bridge.py` is the direct local bridge for a pinned,
operator-installed `panns_inference` runtime and an explicit local checkpoint. It
supports `instrument_detection` and `instrument_diarization`. The host must send
exactly these stage parameters: `window_us`, `hop_us`, `activation_threshold` in
`[0, 1]`, `merge_gap_us`, and a non-empty, case-sensitive `instrument_labels`
allowlist from the pinned checkpoint's taxonomy. The allowlist is required
because PANNs uses the broader AudioSet taxonomy, and non-instrument classes
must not silently become instrument evidence.

The bridge validates native channel/rate metadata, then loads the requested
source span as bounded 10-second mono 32 kHz
chunks, adds the single batch axis expected by
`panns_inference.SoundEventDetection`, and uses the provider's framewise output.
It bounds decoded samples, frame/class dimensions, cells, and accumulated
hypotheses before converting provider arrays or constructing the response. It
suppresses provider stdout and stderr while
the third-party module loads and runs, so status prints cannot corrupt the
bridge's single-JSON-document protocol or leak private paths. It never uses the
clip-level `AudioTagging` API. It turns each allowed active frame into a 10 ms
bin and merges same-label windows only when their gap is no larger than
`merge_gap_us`, preserving simultaneous different-label activity. `window_us`
and `hop_us` must both be `10000`; other values are rejected rather than
silently mis-scaling the frame axis. An explicit local checkpoint path is
mandatory, and the bridge never authorizes a model download.

For each `instrument_diarization` span it records a deterministic run-local
`anonymous_instrument_track_id` (`instrument-01`, and so on) based on sorted
class label. This is an activity-class track, not a claim that PANNs separated
individual performers or source instances. `instrument_detection` omits it.
These outputs remain machine suggestions until a person accepts them.
