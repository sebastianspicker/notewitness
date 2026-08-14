"""Bounded, deterministic local transcript serializers and publication."""

from __future__ import annotations

from collections.abc import Iterator
import errno
from html import escape
import os
from pathlib import Path
import stat
from uuid import uuid4

from notewitness.domain.transcript_document import TranscriptDocument, TranscriptSegment


MAX_RENDERED_CHARACTERS = 10_000_000
_PRIVATE_FILE_MODE = 0o600


class TranscriptPublicationError(RuntimeError):
    """Raised when an export cannot be safely published to local storage."""


def render_txt(
    document: TranscriptDocument,
    *,
    visible_timestamps: bool = True,
    timestamp_interval_ms: int = 1,
    pause_threshold_ms: int | None = None,
) -> str:
    """Render a deterministic, Unicode-preserving plain-text transcript."""

    _require_document(document)
    _render_options(visible_timestamps, timestamp_interval_ms, pause_threshold_ms)
    lines: list[str] = []
    for segment, show_timestamp, pause_duration_us in _rendered_segments(
        document,
        visible_timestamps=visible_timestamps,
        timestamp_interval_ms=timestamp_interval_ms,
        pause_threshold_ms=pause_threshold_ms,
    ):
        pause = _pause_line(pause_duration_us)
        if pause is not None:
            lines.append(pause)
        prefix = f"[{_format_timestamp(segment.start_us)}]" if show_timestamp else ""
        lines.append(
            f"{prefix}{_speaker_prefix(segment.anonymous_speaker_cluster)} "
            f"{_single_line(segment.text)}".strip()
        )
    return _bounded_output("\n".join(lines) + "\n")


def render_webvtt(
    document: TranscriptDocument,
    *,
    visible_timestamps: bool = True,
    timestamp_interval_ms: int = 1,
    pause_threshold_ms: int | None = None,
) -> str:
    """Render source-time ordered, injection-safe WebVTT cues."""

    _require_document(document)
    _render_options(visible_timestamps, timestamp_interval_ms, pause_threshold_ms)
    cues: list[str] = ["WEBVTT", ""]
    for segment in document.segments:
        start_ms = segment.start_us // 1_000
        end_ms = max(start_ms + 1, (segment.end_us + 999) // 1_000)
        if end_ms <= start_ms:
            raise ValueError("WebVTT cues require increasing timestamps.")
        text = _vtt_escape(
            f"{_speaker_prefix(segment.anonymous_speaker_cluster).strip()} "
            f"{_single_line(segment.text)}".strip()
        )
        cues.extend(
            (
                f"{_format_webvtt_milliseconds(start_ms)} --> "
                f"{_format_webvtt_milliseconds(end_ms)}",
                text,
                "",
            )
        )
    return _bounded_output("\n".join(cues))


def render_html(
    document: TranscriptDocument,
    *,
    visible_timestamps: bool = True,
    timestamp_interval_ms: int = 1,
    pause_threshold_ms: int | None = None,
) -> str:
    """Render an accessible, self-contained HTML transcript without raw markup."""

    _require_document(document)
    _render_options(visible_timestamps, timestamp_interval_ms, pause_threshold_ms)
    rendered_entries: list[str] = []
    for segment, show_timestamp, pause_duration_us in _rendered_segments(
        document,
        visible_timestamps=visible_timestamps,
        timestamp_interval_ms=timestamp_interval_ms,
        pause_threshold_ms=pause_threshold_ms,
    ):
        pause = _pause_html(pause_duration_us)
        if pause is not None:
            rendered_entries.append(pause)
        timestamp = (
            "<time datetime=\"{datetime}\">{label}</time>".format(
                datetime=_duration_datetime(segment.start_us),
                label=escape(_format_timestamp(segment.start_us)),
            )
            if show_timestamp
            else ""
        )
        speaker = (
            f"<span class=\"speaker\">"
            f"{escape(segment.anonymous_speaker_cluster)}</span>"
            if segment.anonymous_speaker_cluster is not None
            else ""
        )
        rendered_entries.append(
            f"<li data-start-us=\"{segment.start_us}\" "
            f"data-end-us=\"{segment.end_us}\">{timestamp}{speaker}"
            f"<span class=\"text\">{escape(_single_line(segment.text))}</span></li>"
        )
    entries = "\n".join(rendered_entries)
    language = escape(document.language, quote=True)
    return _bounded_output(
        "<!doctype html>\n"
        f"<html lang=\"{language}\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Transcript</title><style>body{font:1rem/1.5 system-ui,sans-serif;"
        "margin:auto;max-width:72rem;padding:2rem}.transcript{padding-left:2rem}"
        "li{margin:.6rem 0}time{color:#555;font-variant-numeric:tabular-nums;"
        "margin-right:.5rem}.speaker{font-weight:700;margin-right:.5rem}.text{"
        "white-space:pre-wrap}.pause{color:#555;font-style:italic}</style></head>"
        "<body><main><h1>Transcript</h1>"
        f"<ol class=\"transcript\">{entries}</ol></main></body></html>\n"
    )


def publish_new_private_text(path: str | Path, contents: str) -> Path:
    """Exclusively create an owner-private UTF-8 transcript without symlinks."""

    if not isinstance(contents, str):
        raise TranscriptPublicationError("Transcript contents must be text.")
    encoded = contents.encode("utf-8")
    if len(encoded) > MAX_RENDERED_CHARACTERS:
        raise TranscriptPublicationError("Transcript output exceeds its byte limit.")
    target = _absolute_target(Path(path))
    if target == Path(os.path.sep) or target.name in {"", ".", ".."}:
        raise TranscriptPublicationError(f"Invalid transcript path: {path}")
    try:
        parent_descriptor = _open_private_parent(target.parent)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise TranscriptPublicationError(
                "Transcript parent must be an existing, non-symlink directory."
            ) from exc
        raise
    try:
        _write_exclusive_private_file(parent_descriptor, target.name, encoded)
    finally:
        os.close(parent_descriptor)
    return target


def _require_document(document: object) -> None:
    if not isinstance(document, TranscriptDocument):
        raise ValueError("Transcript writers require a TranscriptDocument.")


def _single_line(text: str) -> str:
    return " ".join(text.split())


def _speaker_prefix(cluster: str | None) -> str:
    return f" [{cluster}]" if cluster is not None else ""


def _render_options(
    visible_timestamps: bool,
    timestamp_interval_ms: int,
    pause_threshold_ms: int | None,
) -> None:
    if not isinstance(visible_timestamps, bool):
        raise ValueError("visible_timestamps must be a boolean.")
    if (
        not isinstance(timestamp_interval_ms, int)
        or isinstance(timestamp_interval_ms, bool)
        or timestamp_interval_ms <= 0
    ):
        raise ValueError("timestamp_interval_ms must be a positive integer.")
    if pause_threshold_ms not in {None, 1_000, 2_000, 3_000}:
        raise ValueError("pause_threshold_ms must be off, 1000, 2000, or 3000.")


def _rendered_segments(
    document: TranscriptDocument,
    *,
    visible_timestamps: bool,
    timestamp_interval_ms: int,
    pause_threshold_ms: int | None,
) -> Iterator[tuple[TranscriptSegment, bool, int | None]]:
    """Yield TXT/HTML segments with their shared timestamp and pause decisions."""

    next_timestamp_us = 0
    previous_end_us: int | None = None
    for segment in document.segments:
        pause_duration_us = _pause_duration_us(
            previous_end_us,
            segment.start_us,
            pause_threshold_ms,
        )
        show_timestamp = visible_timestamps and segment.start_us >= next_timestamp_us
        if show_timestamp:
            next_timestamp_us = segment.start_us + timestamp_interval_ms * 1_000
        yield segment, show_timestamp, pause_duration_us
        previous_end_us = segment.end_us


def _pause_duration_us(
    previous_end_us: int | None,
    start_us: int,
    threshold_ms: int | None,
) -> int | None:
    if previous_end_us is None or threshold_ms is None:
        return None
    gap_us = max(0, start_us - previous_end_us)
    if gap_us < threshold_ms * 1_000:
        return None
    return gap_us


def _pause_line(pause_duration_us: int | None) -> str | None:
    if pause_duration_us is None:
        return None
    return f"[PAUSE {pause_duration_us / 1_000_000:.3f} s]"


def _pause_html(pause_duration_us: int | None) -> str | None:
    line = _pause_line(pause_duration_us)
    if line is None:
        return None
    return (
        f"<li class=\"pause\" data-duration-us=\"{pause_duration_us}\">"
        f"{escape(line)}</li>"
    )


def _format_timestamp(microseconds: int) -> str:
    return _format_webvtt_milliseconds(microseconds // 1_000)


def _format_webvtt_milliseconds(total_milliseconds: int) -> str:
    if not isinstance(total_milliseconds, int) or total_milliseconds < 0:
        raise ValueError("WebVTT timestamps must be non-negative milliseconds.")
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _duration_datetime(microseconds: int) -> str:
    milliseconds = microseconds // 1_000
    seconds, remainder = divmod(milliseconds, 1_000)
    return f"PT{seconds}.{remainder:03d}S"


def _vtt_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _bounded_output(value: str) -> str:
    if len(value) > MAX_RENDERED_CHARACTERS:
        raise ValueError("Rendered transcript exceeds its character limit.")
    return value


def _absolute_target(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _open_private_parent(directory: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(os.path.sep, flags)
    try:
        for component in directory.parts[1:]:
            child_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child_descriptor
        mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
        if mode & 0o077:
            raise TranscriptPublicationError(
                "Transcript parent must deny group and other access "
                f"(current mode: {mode:04o})."
            )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_exclusive_private_file(directory_descriptor: int, name: str, contents: bytes) -> None:
    temporary_name = f".{name}.{uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            _PRIVATE_FILE_MODE,
            dir_fd=directory_descriptor,
        )
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.link(
            temporary_name,
            name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        os.unlink(temporary_name, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
    except FileExistsError as exc:
        raise TranscriptPublicationError(
            f"Refusing to replace existing transcript path: {name}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
