#!/usr/bin/env python3
"""Fail closed when the prospective public file set contains local/private residue."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BANNED_COMPONENTS = frozenset(
    {
        "archive",
        "artifacts",
        "data",
        "media",
        "models",
        "projects",
    }
)
BANNED_FILENAMES = frozenset({".DS_Store", "Thumbs.db"})
BANNED_SUFFIXES = frozenset(
    {
        ".aac",
        ".aif",
        ".aiff",
        ".bak",
        ".caf",
        ".db",
        ".flac",
        ".key",
        ".log",
        ".m4a",
        ".mei",
        ".mid",
        ".midi",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".mscz",
        ".musicxml",
        ".mxl",
        ".ogg",
        ".opus",
        ".orig",
        ".p12",
        ".pem",
        ".pfx",
        ".rej",
        ".sqlite",
        ".sqlite3",
        ".tmp",
        ".wav",
        ".webm",
    }
)
SENSITIVE_TEXT_PATTERNS = (
    ("absolute Unix home path", re.compile(r"/(?:Users|home)/[^/\s]+/")),
    ("absolute Windows home path", re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+[\\/]")),
    (
        "private key material",
        re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    ),
    (
        "credential-shaped token",
        re.compile(
            r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
            r"(?:sk|rk)-(?:proj-)?[A-Za-z0-9_-]{20,})\b"
        ),
    ),
    (
        "email address",
        re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
)
TEXT_SUFFIXES = frozenset(
    {
        "",
        ".css",
        ".html",
        ".js",
        ".json",
        ".jsonld",
        ".md",
        ".mjs",
        ".py",
        ".sh",
        ".svg",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)


def candidate_paths() -> tuple[Path, ...]:
    result = subprocess.run(
        ("git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"),
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(
        Path(value.decode("utf-8"))
        for value in result.stdout.split(b"\0")
        if value
    )


def path_violations(path: Path) -> tuple[str, ...]:
    lowered_parts = {part.casefold() for part in path.parts}
    problems: list[str] = []
    if lowered_parts & BANNED_COMPONENTS:
        problems.append("local or private directory")
    if path.name in BANNED_FILENAMES:
        problems.append("local operating-system metadata")
    if path.name == ".env" or path.name.startswith(".env."):
        problems.append("environment file")
    if path.suffix.casefold() in BANNED_SUFFIXES:
        problems.append("private artifact extension")
    if tuple(part.casefold() for part in path.parts[:3]) == (
        "docs",
        "screenshots",
        "generated",
    ):
        problems.append("raw screenshot output")
    return tuple(problems)


def content_violations(path: Path) -> tuple[str, ...]:
    absolute = ROOT / path
    if path.suffix.casefold() not in TEXT_SUFFIXES or absolute.stat().st_size > 2 * 1024 * 1024:
        return ()
    contents = absolute.read_text(encoding="utf-8", errors="strict")
    return text_violations(contents)


def text_violations(contents: str) -> tuple[str, ...]:
    return tuple(
        label for label, pattern in SENSITIVE_TEXT_PATTERNS if pattern.search(contents)
    )


def main() -> int:
    failures: list[str] = []
    for path in candidate_paths():
        if not (ROOT / path).is_file():
            continue
        for problem in (*path_violations(path), *content_violations(path)):
            failures.append(f"{path}: {problem}")
    if failures:
        print("Public hygiene check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Public hygiene check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
