from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
import unittest

from notewitness.adapters.analysis_cli import (
    AnalysisCLICancelled,
    AnalysisCLIError,
    AnalysisCLIExecutionError,
    LocalAnalysisCLIAdapter,
    LocalAnalysisCLISettings,
    LocalAnalysisSource,
    analysis_artifact_identity,
)
from notewitness.domain.analysis import (
    AnalysisRequest,
    AnalysisStage,
    AnalysisState,
    InstrumentHypothesis,
    NoteHypothesis,
    SpeakerSegmentHypothesis,
)
from notewitness.domain.timeline import MediaSpan
from notewitness.local_tools import LocalTool, LocalToolCancelled


@unittest.skipUnless(platform.system() == "Darwin", "macOS isolation contract")
class LocalAnalysisCLIAdapterTests(unittest.TestCase):
    def test_all_supported_stages_parse_typed_suggestions(self) -> None:
        for stage, hypothesis in _stage_payloads().items():
            with self.subTest(stage=stage), TemporaryDirectory() as temporary:
                root = _private_root(temporary)
                adapter = _adapter(root, stage, _output(hypothesis))
                batch = adapter.analyze(_request())
                self.assertEqual(stage, batch.result.stage)
                self.assertEqual(AnalysisState.READY, batch.result.state)
                self.assertEqual("generator:fixture", batch.hypotheses[0].generator_id)
                if stage is AnalysisStage.NOTE_TRANSCRIPTION:
                    note = batch.hypotheses[0]
                    self.assertIsInstance(note, NoteHypothesis)
                    assert isinstance(note, NoteHypothesis)
                    self.assertEqual("instrument-track-01", note.source_track_id)
                    self.assertEqual(0.74, note.amplitude)
                    self.assertEqual(91, note.velocity)
                    self.assertEqual((-0.25, 0.5), note.pitch_bend_values)
                    self.assertEqual("semitones", note.pitch_bend_unit)
                if stage is AnalysisStage.INSTRUMENT_DIARIZATION:
                    instrument = batch.hypotheses[0]
                    self.assertIsInstance(instrument, InstrumentHypothesis)
                    assert isinstance(instrument, InstrumentHypothesis)
                    self.assertEqual(
                        "instrument-track-01",
                        instrument.anonymous_instrument_track_id,
                    )

                execution = adapter.execute(_request())
                self.assertEqual(batch, execution.batch)
                self.assertTrue(execution.network_isolated)
                self.assertEqual(
                    hashlib.sha256(execution.raw_output).hexdigest(),
                    execution.raw_output_sha256,
                )

    def test_diarization_preserves_overlapping_anonymous_spans(self) -> None:
        root_context = TemporaryDirectory()
        self.addCleanup(root_context.cleanup)
        root = _private_root(root_context.name)
        output = _output(
            {
                "hypothesis_id": "speaker:one",
                "span": {"stream_id": "audio", "start_us": 100, "duration_us": 500},
                "state": "ready",
                "confidence": 0.8,
                "anonymous_cluster_id": "SPEAKER_00",
            },
            {
                "hypothesis_id": "speaker:two",
                "span": {"stream_id": "audio", "start_us": 400, "duration_us": 500},
                "state": "ready",
                "confidence": 0.7,
                "anonymous_cluster_id": "SPEAKER_01",
            },
        )
        batch = _adapter(root, AnalysisStage.ANONYMOUS_DIARIZATION, output).analyze(_request())
        first, second = batch.hypotheses
        self.assertIsInstance(first, SpeakerSegmentHypothesis)
        self.assertLess(second.span.start_us, first.span.end_us)
        self.assertEqual("SPEAKER_01", second.anonymous_cluster_id)

    def test_rejects_malformed_and_out_of_span_output(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _private_root(temporary)
            malformed = '{"state":"ready","hypotheses":[],"diagnostics":[],"extra":true}'
            adapter = _adapter(root, AnalysisStage.CONTINUOUS_PITCH, malformed)
            with self.assertRaises(AnalysisCLIError):
                adapter.analyze(_request())
            outside = _output(
                {
                    "hypothesis_id": "pitch:outside",
                    "span": {"stream_id": "audio", "start_us": 0, "duration_us": 1_001},
                    "state": "ready",
                    "confidence": 0.5,
                    "frequency_hz": 440.0,
                }
            )
            with self.assertRaises(AnalysisCLIExecutionError) as caught:
                _adapter(root, AnalysisStage.CONTINUOUS_PITCH, outside).analyze(_request())
            self.assertRegex(str(caught.exception.__cause__), "outside")

    def test_request_never_serializes_media_paths_and_runner_is_network_denied(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _private_root(temporary)
            adapter = _adapter(
                root,
                AnalysisStage.CONTINUOUS_PITCH,
                _output(_stage_payloads()[AnalysisStage.CONTINUOUS_PITCH]),
            )
            with self.assertRaisesRegex(AnalysisCLIError, "media paths"):
                adapter.analyze(
                    AnalysisRequest(
                        "job:one",
                        "source:one",
                        _request().spans,
                        {"media_path": "/private/media.wav"},
                    )
                )
            calls: list[bool] = []
            original_run = adapter._runner.run

            def observed_run(*args: object, **kwargs: object) -> object:
                calls.append(kwargs["deny_network"] is True)
                return original_run(*args, **kwargs)  # type: ignore[arg-type]

            adapter._runner.run = observed_run  # type: ignore[method-assign]
            adapter.analyze(_request())
            self.assertEqual([True], calls)

    def test_runtime_owned_media_identity_is_sent_and_rechecked(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _private_root(temporary)
            adapter = _adapter(
                root,
                AnalysisStage.CONTINUOUS_PITCH,
                _output(_stage_payloads()[AnalysisStage.CONTINUOUS_PITCH]),
            )

            adapter.analyze(_request())
            adapter.settings.media.path.write_bytes(b"mutated")

            with self.assertRaisesRegex(AnalysisCLIError, "changed"):
                adapter.analyze(_request())

    def test_model_directory_identity_is_recursive_and_symlink_free(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _private_root(temporary)
            model = root / "pyannote-community-1"
            model.mkdir(mode=0o700)
            nested = model / "weights"
            nested.mkdir(mode=0o700)
            config = model / "config.yaml"
            config.write_bytes(b"pipeline: local\n")
            config.chmod(0o600)
            weights = nested / "model.bin"
            weights.write_bytes(b"fixed local weights")
            weights.chmod(0o600)

            first = analysis_artifact_identity(model)
            source = LocalAnalysisSource("model:pyannote", model, *first)
            self.assertEqual(model, source.path)
            weights.write_bytes(b"changed local weights")
            weights.chmod(0o600)
            self.assertNotEqual(first, analysis_artifact_identity(model))

            alias = model / "weights-alias"
            alias.symlink_to(weights)
            with self.assertRaisesRegex(ValueError, "symlink-free"):
                analysis_artifact_identity(model)

    def test_executable_identity_is_checked_before_and_after_execution(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _private_root(temporary)
            adapter = _adapter(
                root,
                AnalysisStage.CONTINUOUS_PITCH,
                _output(_stage_payloads()[AnalysisStage.CONTINUOUS_PITCH]),
            )
            calls = 0
            original_run = adapter._runner.run

            def observed_run(*args: object, **kwargs: object) -> object:
                nonlocal calls
                calls += 1
                return original_run(*args, **kwargs)  # type: ignore[arg-type]

            adapter._runner.run = observed_run  # type: ignore[method-assign]
            adapter.tool.executable.write_text(
                "#!/bin/sh\nexit 7\n",
                encoding="utf-8",
            )
            adapter.tool.executable.chmod(0o700)

            with self.assertRaisesRegex(AnalysisCLIError, "executable changed"):
                adapter.execute(_request())
            self.assertEqual(0, calls)

        with TemporaryDirectory() as temporary:
            root = _private_root(temporary)
            adapter = _adapter(
                root,
                AnalysisStage.CONTINUOUS_PITCH,
                _output(_stage_payloads()[AnalysisStage.CONTINUOUS_PITCH]),
            )
            original_run = adapter._runner.run

            def mutating_run(*args: object, **kwargs: object) -> object:
                result = original_run(*args, **kwargs)  # type: ignore[arg-type]
                adapter.tool.executable.write_text(
                    "#!/bin/sh\nexit 8\n",
                    encoding="utf-8",
                )
                adapter.tool.executable.chmod(0o700)
                return result

            adapter._runner.run = mutating_run  # type: ignore[method-assign]

            with self.assertRaisesRegex(AnalysisCLIError, "executable changed"):
                adapter.execute(_request())

    def test_runner_cancellation_has_a_distinct_adapter_outcome(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _private_root(temporary)
            adapter = _adapter(
                root,
                AnalysisStage.CONTINUOUS_PITCH,
                _output(_stage_payloads()[AnalysisStage.CONTINUOUS_PITCH]),
            )
            probes: list[object] = []

            def cancelled_run(*args: object, **kwargs: object) -> object:
                probes.append(kwargs["cancellation_requested"])
                raise LocalToolCancelled("fixture cancellation")

            adapter._runner.run = cancelled_run  # type: ignore[method-assign]
            cancellation_requested = lambda: True

            with self.assertRaises(AnalysisCLICancelled):
                adapter.execute(
                    _request(),
                    cancellation_requested=cancellation_requested,
                )

            self.assertEqual([cancellation_requested], probes)


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        "job:one",
        "source:one",
        (MediaSpan("source:one", "audio", 0, 1_000),),
        {"window_us": 1_000},
        "continue:one",
    )


def _stage_payloads() -> dict[AnalysisStage, dict[str, object]]:
    common: dict[str, object] = {
        "span": {"stream_id": "audio", "start_us": 100, "duration_us": 500},
        "state": "ready",
        "confidence": 0.8,
    }
    return {
        AnalysisStage.ACTIVITY_SEGMENTATION: {
            **common,
            "hypothesis_id": "activity:one",
            "kind": "music",
        },
        AnalysisStage.ANONYMOUS_DIARIZATION: {
            **common,
            "hypothesis_id": "speaker:one",
            "anonymous_cluster_id": "SPEAKER_00",
        },
        AnalysisStage.NOTE_TRANSCRIPTION: {
            **common,
            "hypothesis_id": "note:one",
            "midi_pitch": 69.0,
            "frequency_hz": 440.0,
            "source_track_id": "instrument-track-01",
            "amplitude": 0.74,
            "velocity": 91,
            "pitch_bend_values": [-0.25, 0.5],
            "pitch_bend_unit": "semitones",
        },
        AnalysisStage.CONTINUOUS_PITCH: {
            **common,
            "hypothesis_id": "pitch:one",
            "frequency_hz": 440.0,
        },
        AnalysisStage.INSTRUMENT_DETECTION: {
            **common,
            "hypothesis_id": "instrument:one",
            "instrument_label": "piano",
        },
        AnalysisStage.INSTRUMENT_DIARIZATION: {
            **common,
            "hypothesis_id": "instrument-track:one",
            "instrument_label": "piano",
            "anonymous_instrument_track_id": "instrument-track-01",
        },
        AnalysisStage.SCORE_ALIGNMENT: {
            **common,
            "hypothesis_id": "alignment:one",
            "outcome": "aligned",
            "score_id": "score:one",
            "score_position": {"measure": 2},
            "source_hypothesis_ids": ["note:one"],
        },
    }


def _output(*hypotheses: dict[str, object]) -> str:
    return json.dumps(
        {
            "state": "ready",
            "hypotheses": list(hypotheses),
            "diagnostics": [],
            "continuation_token": None,
        }
    )


def _private_root(raw: str) -> Path:
    root = Path(raw).resolve()
    root.chmod(0o700)
    return root


def _adapter(root: Path, stage: AnalysisStage, output: str) -> LocalAnalysisCLIAdapter:
    media = root / "lesson.wav"
    media.write_bytes(b"synthetic local media")
    media.chmod(0o600)
    media_raw = media.read_bytes()
    model = root / "analysis.model"
    model.write_bytes(b"synthetic local model")
    model.chmod(0o600)
    model_raw = model.read_bytes()
    script = root / f"analysis-{stage.value}"
    script.write_text(
        "#!/usr/bin/python3\n"
        "import json, pathlib, sys\n"
        "request = json.loads(pathlib.Path(sys.argv[2]).read_text())\n"
        "assert sys.argv[1] == '--request'\n"
        "assert 'media_path' not in request['parameters']\n"
        "assert pathlib.Path(request['media']['path']).is_file()\n"
        "assert pathlib.Path(request['model']['path']).is_file()\n"
        f"print({output!r})\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    return LocalAnalysisCLIAdapter(
        LocalTool("analysis-cli", script),
        stage=stage,
        version="test-v1",
        generator_id="generator:fixture",
        settings=LocalAnalysisCLISettings(
            root,
            media=LocalAnalysisSource(
                source_id="source:one",
                path=media,
                sha256=hashlib.sha256(media_raw).hexdigest(),
                size_bytes=len(media_raw),
            ),
            model=LocalAnalysisSource(
                source_id="model:fixture",
                path=model,
                sha256=hashlib.sha256(model_raw).hexdigest(),
                size_bytes=len(model_raw),
            ),
            model_license="LicenseRef-test-model",
            adapter_license="MIT-test-adapter",
            timeout_seconds=30,
        ),
    )


if __name__ == "__main__":
    unittest.main()
