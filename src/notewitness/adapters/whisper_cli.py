"""Offline OpenAI Whisper CLI adapter with strict artifact provenance."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import stat
from typing import Any, Callable

from notewitness.domain.transcript_document import (
    MAX_SEGMENTS,
    MAX_WORDS,
    TranscriptDocument,
    TranscriptSegment,
    TranscriptWord,
)
from notewitness.local_tools import (
    BoundedLocalToolRunner,
    LocalTool,
    LocalToolIdentityChanged,
)


MAX_RAW_TRANSCRIPT_BYTES = 128 * 1024 * 1024
MAX_MODEL_BYTES = 20 * 1024 * 1024 * 1024
MAX_JSON_DEPTH = 64
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


class WhisperCLIError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WhisperCLISettings:
    model_checkpoint: Path
    model_license: str
    adapter_license: str
    ffmpeg_license: str | None = None
    language: str | None = None
    beam_size: int = 5
    threads: int = 0
    device: str = "cpu"
    timeout_seconds: int = 7_200

    def __post_init__(self) -> None:
        model = _validated_regular_file(
            self.model_checkpoint,
            label="Whisper model checkpoint",
            maximum_bytes=MAX_MODEL_BYTES,
        )
        object.__setattr__(self, "model_checkpoint", model)
        if not self.model_license.strip():
            raise ValueError("Whisper model checkpoint requires an explicit license.")
        if not self.adapter_license.strip():
            raise ValueError("Whisper executable requires an explicit license.")
        if self.language is not None and not _LANGUAGE.fullmatch(self.language):
            raise ValueError("Whisper language must be a normalized BCP-47 tag.")
        if (
            not isinstance(self.beam_size, int)
            or isinstance(self.beam_size, bool)
            or not 1 <= self.beam_size <= 100
        ):
            raise ValueError("Whisper beam_size must be in [1, 100].")
        if (
            not isinstance(self.threads, int)
            or isinstance(self.threads, bool)
            or not 0 <= self.threads <= 256
        ):
            raise ValueError("Whisper threads must be in [0, 256].")
        if self.device not in {"cpu", "cuda", "mps"}:
            raise ValueError("Whisper device must be cpu, cuda, or mps.")
        if (
            not isinstance(self.timeout_seconds, int)
            or isinstance(self.timeout_seconds, bool)
            or not 1 <= self.timeout_seconds <= 43_200
        ):
            raise ValueError("Whisper timeout must be in [1, 43200] seconds.")


@dataclass(frozen=True, slots=True)
class WhisperArtifactIdentity:
    path_name: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class WhisperCLIResult:
    document: TranscriptDocument
    raw_output_path: Path
    raw_output: WhisperArtifactIdentity
    model: WhisperArtifactIdentity
    launcher: WhisperArtifactIdentity
    ffmpeg: WhisperArtifactIdentity | None
    runtime_fingerprint_sha256: str
    duration_ms: int
    network_isolated: bool

    @property
    def adapter_code(self) -> WhisperArtifactIdentity:
        """Compatibility alias for launcher bytes, not complete runtime provenance."""
        return self.launcher


class WhisperCLIAdapter:
    """Run one explicit local checkpoint; model names/downloads are not accepted."""

    def __init__(
        self,
        tool: LocalTool,
        settings: WhisperCLISettings,
        *,
        ffmpeg: LocalTool | None = None,
    ) -> None:
        if tool.name != "whisper":
            raise ValueError("WhisperCLIAdapter requires the whisper tool.")
        if ffmpeg is not None and ffmpeg.name != "ffmpeg":
            raise ValueError("WhisperCLIAdapter requires an ffmpeg tool.")
        if ffmpeg is not None and ffmpeg.executable.name != "ffmpeg":
            raise ValueError(
                "WhisperCLIAdapter requires the approved ffmpeg executable "
                "to have the exact basename 'ffmpeg'."
            )
        if ffmpeg is not None and not (settings.ffmpeg_license or "").strip():
            raise ValueError("The explicit ffmpeg executable requires a license.")
        self.tool = tool
        self.settings = settings
        self.ffmpeg = ffmpeg
        self._runner = BoundedLocalToolRunner(tool)

    def transcribe(
        self,
        *,
        media_path: str | Path,
        output_directory: str | Path,
        source_id: str,
        stream_id: str,
        run_id: str,
        raw_artifact_id: str,
        duration_us: int,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> WhisperCLIResult:
        media = _validated_regular_file(
            Path(media_path),
            label="Whisper media source",
            maximum_bytes=None,
        )
        output = _validated_private_directory(Path(output_directory))
        if not source_id or not stream_id or not run_id or not raw_artifact_id:
            raise ValueError("Whisper normalization requires source and run identity.")
        if (
            not isinstance(duration_us, int)
            or isinstance(duration_us, bool)
            or duration_us <= 0
        ):
            raise ValueError("Whisper media duration must be positive microseconds.")
        expected_raw = output / f"{media.stem}.json"
        if expected_raw.exists() or expected_raw.is_symlink():
            raise WhisperCLIError("Whisper raw output path already exists.")

        _media_identity, media_stat = _stable_artifact_identity(
            media, "Whisper media source"
        )
        model_before, model_stat = _stable_artifact_identity(
            self.settings.model_checkpoint, "Whisper model checkpoint"
        )
        launcher_before, launcher_stat = _stable_artifact_identity(
            self.tool.executable, "Whisper launcher"
        )
        ffmpeg_before: WhisperArtifactIdentity | None = None
        ffmpeg_stat: os.stat_result | None = None
        if self.ffmpeg is not None:
            ffmpeg_before, ffmpeg_stat = _stable_artifact_identity(
                self.ffmpeg.executable, "ffmpeg executable"
            )
        arguments = [
            str(media),
            "--model",
            str(self.settings.model_checkpoint),
            "--output_dir",
            str(output),
            "--output_format",
            "json",
            "--verbose",
            "False",
            "--word_timestamps",
            "True",
            "--beam_size",
            str(self.settings.beam_size),
            "--device",
            self.settings.device,
        ]
        if self.settings.device == "cpu":
            arguments.extend(("--fp16", "False"))
        if self.settings.threads:
            arguments.extend(("--threads", str(self.settings.threads)))
        if self.settings.language is not None:
            arguments.extend(("--language", self.settings.language))
        self._require_tool_identities()
        try:
            result = self._runner.run(
                tuple(arguments),
                working_directory=output,
                timeout_seconds=self.settings.timeout_seconds,
                deny_network=True,
                environment=(
                    {"OMP_NUM_THREADS": str(self.settings.threads)}
                    if self.settings.threads
                    else None
                ),
                executable_search_paths=(
                    (self.ffmpeg.executable.parent,) if self.ffmpeg is not None else ()
                ),
                cancellation_requested=cancellation_requested,
            )
        finally:
            # ffmpeg is selected by PATH inside Whisper, so it is an execution
            # dependency despite not being the runner's direct executable.
            # Keep its startup-approved identity bound over every exit path.
            self._require_tool_identities()
        _require_unchanged(
            self.settings.model_checkpoint,
            model_stat,
            "Whisper model checkpoint",
        )
        _require_unchanged(self.tool.executable, launcher_stat, "Whisper launcher")
        if self.ffmpeg is not None and ffmpeg_stat is not None:
            _require_unchanged(
                self.ffmpeg.executable,
                ffmpeg_stat,
                "ffmpeg executable",
            )
        _require_unchanged(media, media_stat, "Whisper media source")
        raw_payload, raw_identity = _read_raw_output(expected_raw)
        document = _normalize_document(
            raw_payload,
            source_id=source_id,
            stream_id=stream_id,
            run_id=run_id,
            raw_artifact_id=raw_artifact_id,
            requested_language=self.settings.language,
            duration_us=duration_us,
        )
        return WhisperCLIResult(
            document=document,
            raw_output_path=expected_raw,
            raw_output=raw_identity,
            model=model_before,
            launcher=launcher_before,
            ffmpeg=ffmpeg_before,
            runtime_fingerprint_sha256=_runtime_fingerprint(
                model_before,
                launcher_before,
                ffmpeg_before,
                self.settings,
            ),
            duration_ms=result.duration_ms,
            network_isolated=result.network_isolated,
        )

    def _require_tool_identities(self) -> None:
        try:
            self.tool.require_unchanged()
            if self.ffmpeg is not None:
                self.ffmpeg.require_unchanged()
        except LocalToolIdentityChanged as exc:
            raise WhisperCLIError(
                "Whisper launcher or configured ffmpeg changed after startup approval."
            ) from exc


def _normalize_document(
    payload: dict[str, Any],
    *,
    source_id: str,
    stream_id: str,
    run_id: str,
    raw_artifact_id: str,
    requested_language: str | None,
    duration_us: int,
) -> TranscriptDocument:
    detected = payload.get("language")
    language = detected if isinstance(detected, str) else requested_language or "und"
    language = language.strip().replace("_", "-")
    if not _LANGUAGE.fullmatch(language):
        raise WhisperCLIError("Whisper output language is not a normalized tag.")
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list) or len(raw_segments) > MAX_SEGMENTS:
        raise WhisperCLIError("Whisper output has invalid or unbounded segments.")

    segments: list[TranscriptSegment] = []
    words: list[TranscriptWord] = []
    word_index = 0
    previous_segment_start: int | None = None
    run_token = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    for segment_index, raw_segment in enumerate(raw_segments):
        if not isinstance(raw_segment, dict):
            raise WhisperCLIError("Whisper segments must be JSON objects.")
        text = _text(raw_segment.get("text"))
        if text is None:
            continue
        segment_start = _seconds_to_microseconds(raw_segment.get("start"), "segment start")
        segment_end = _seconds_to_microseconds(raw_segment.get("end"), "segment end")
        raw_words = raw_segment.get("words", [])
        if not isinstance(raw_words, list):
            raise WhisperCLIError("Whisper segment words must be an array.")
        if len(words) + len(raw_words) > MAX_WORDS:
            raise WhisperCLIError("Whisper output exceeds the normalized word bound.")
        segment_words: list[TranscriptWord] = []
        previous_word_start: int | None = None
        for raw_word in raw_words:
            if not isinstance(raw_word, dict):
                raise WhisperCLIError("Whisper words must be JSON objects.")
            word_text = _text(raw_word.get("word"))
            if word_text is None:
                continue
            word_start = _seconds_to_microseconds(raw_word.get("start"), "word start")
            word_end = _seconds_to_microseconds(raw_word.get("end"), "word end")
            if previous_word_start is not None and word_start < previous_word_start:
                raise WhisperCLIError("Whisper words must use nondecreasing start times.")
            previous_word_start = word_start
            confidence = _probability(raw_word.get("probability"), "word probability")
            word_index += 1
            segment_words.append(
                TranscriptWord(
                    word_id=f"word:{run_token}:{word_index}",
                    source_id=source_id,
                    stream_id=stream_id,
                    start_us=word_start,
                    end_us=word_end,
                    text=word_text,
                    language=language,
                    confidence=confidence,
                )
            )
        if segment_words:
            segment_start = min(segment_start, segment_words[0].start_us)
            segment_end = max(segment_end, segment_words[-1].end_us)
        if segment_start < 0 or segment_end <= segment_start or segment_end > duration_us:
            raise WhisperCLIError("Whisper segment timing is outside the source duration.")
        if previous_segment_start is not None and segment_start < previous_segment_start:
            raise WhisperCLIError("Whisper segments must use nondecreasing start times.")
        previous_segment_start = segment_start
        if any(
            word.start_us < segment_start or word.end_us > segment_end
            for word in segment_words
        ):
            raise WhisperCLIError("Whisper word timing is outside its segment.")
        words.extend(segment_words)
        segments.append(
            TranscriptSegment(
                segment_id=f"segment:{run_token}:{segment_index + 1}",
                source_id=source_id,
                stream_id=stream_id,
                start_us=segment_start,
                end_us=segment_end,
                text=text,
                language=language,
                confidence=_segment_confidence(raw_segment),
                word_ids=tuple(word.word_id for word in segment_words),
            )
        )
    return TranscriptDocument(
        document_id=f"transcript:{run_token}",
        source_id=source_id,
        stream_id=stream_id,
        raw_artifact_id=raw_artifact_id,
        run_id=run_id,
        language=language,
        segments=tuple(segments),
        words=tuple(words),
    )


def _read_raw_output(path: Path) -> tuple[dict[str, Any], WhisperArtifactIdentity]:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_RAW_TRANSCRIPT_BYTES:
            raise WhisperCLIError("Whisper raw output is not a bounded regular file.")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining:
            raise WhisperCLIError("Whisper raw output changed while reading.")
        raw = b"".join(chunks)
        _require_same_stat(metadata, os.fstat(descriptor), "Whisper raw output")
    except OSError as exc:
        raise WhisperCLIError("Whisper did not produce a safe raw JSON artifact.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WhisperCLIError(f"Whisper raw output duplicated key {key!r}.")
            result[key] = value
        return result

    try:
        payload = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise WhisperCLIError("Whisper raw output is invalid JSON.") from exc
    if not isinstance(payload, dict) or _json_depth(payload) > MAX_JSON_DEPTH:
        raise WhisperCLIError("Whisper raw output is not a bounded JSON object.")
    return payload, WhisperArtifactIdentity(
        path.name, hashlib.sha256(raw).hexdigest(), len(raw)
    )


def _validated_regular_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int | None,
) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError(f"{label} must be an absolute, non-symlink path.")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise ValueError(f"{label} is unavailable.") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise ValueError(f"{label} must be a non-empty regular file.")
    if maximum_bytes is not None and metadata.st_size > maximum_bytes:
        raise ValueError(f"{label} exceeds its supported size bound.")
    return resolved


def _validated_private_directory(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Whisper output directory must be absolute and non-symlinked.")
    resolved = path.resolve(strict=True)
    metadata = resolved.stat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("Whisper output directory must deny group and other access.")
    return resolved


def _stable_artifact_identity(
    path: Path, label: str
) -> tuple[WhisperArtifactIdentity, os.stat_result]:
    """Hash one stable local artifact; this identifies bytes, not a runtime stack."""
    digest = hashlib.sha256()
    size = 0
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise WhisperCLIError(f"{label} is not a regular file.")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise WhisperCLIError(f"{label} could not be read safely.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _require_same_stat(before, after, label)
    _require_same_stat(before, path.stat(), label)
    return WhisperArtifactIdentity(path.name, digest.hexdigest(), size), before


def _require_unchanged(path: Path, before: os.stat_result, label: str) -> None:
    _require_same_stat(before, path.stat(), label)


def _require_same_stat(
    before: os.stat_result, after: os.stat_result, label: str
) -> None:
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after:
        raise WhisperCLIError(f"{label} changed during identity verification or transcription.")


def _runtime_fingerprint(
    model: WhisperArtifactIdentity,
    launcher: WhisperArtifactIdentity,
    ffmpeg: WhisperArtifactIdentity | None,
    settings: WhisperCLISettings,
) -> str:
    payload = {
        "launcher_sha256": launcher.sha256,
        "ffmpeg_sha256": ffmpeg.sha256 if ffmpeg is not None else None,
        "beam_size": settings.beam_size,
        "device": settings.device,
        "machine": platform.machine(),
        "model_sha256": model.sha256,
        "system": platform.system(),
        "system_release": platform.release(),
        "threads": settings.threads,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _seconds_to_microseconds(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WhisperCLIError(f"Whisper {label} must be numeric.")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise WhisperCLIError(f"Whisper {label} must be finite and non-negative.")
    return round(numeric * 1_000_000)


def _probability(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WhisperCLIError(f"Whisper {label} must be numeric.")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0 <= numeric <= 1:
        raise WhisperCLIError(f"Whisper {label} must be in [0, 1].")
    return numeric


def _segment_confidence(segment: dict[str, Any]) -> float:
    value = segment.get("avg_logprob")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    numeric = float(value)
    if not math.isfinite(numeric):
        return 0.0
    return min(1.0, max(0.0, math.exp(numeric)))


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        raise WhisperCLIError("Whisper transcript text must be a string.")
    normalized = " ".join(value.split())
    return normalized or None


def _json_depth(value: Any) -> int:
    maximum = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        maximum = max(maximum, depth)
        if maximum > MAX_JSON_DEPTH:
            return maximum
        if isinstance(item, dict):
            stack.extend((nested, depth + 1) for nested in item.values())
        elif isinstance(item, list):
            stack.extend((nested, depth + 1) for nested in item)
    return maximum
