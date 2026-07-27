from __future__ import annotations

import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from notewitness.media_ingest import (
    MAX_INGEST_BYTES,
    MediaIngestError,
    MediaMetadata,
    _same_source_snapshot,
    ingest_media,
)
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


class FixedProbe:
    def probe(self, source_path: Path) -> MediaMetadata:
        self.source_path = source_path
        return MediaMetadata("audio", duration_us=1_250_000, stream_count=1)


class MediaIngestTests(unittest.TestCase):
    def test_streams_private_media_and_publishes_valid_restricted_source(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "study"
            initialize_project(root)
            source = parent / "lesson.wav"
            source.write_bytes(b"test music lesson\x00")
            probe = FixedProbe()

            imported = ingest_media(
                root, source, create_restricted_rights=True, probe=probe
            )

            destination = root / imported.relative_path
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), imported.sha256)
            self.assertEqual(source.read_bytes(), destination.read_bytes())
            self.assertEqual(0o600, destination.stat().st_mode & 0o777)
            self.assertEqual("restricted", imported.project.payload["rights"][0]["access"])
            self.assertFalse(imported.project.payload["rights"][0]["remote_processing"])
            self.assertEqual(imported.relative_path, imported.project.payload["sources"][0]["uri"])
            self.assertEqual(
                1_250_000, imported.metadata.duration_us if imported.metadata else None
            )
            self.assertTrue(destination.samefile(probe.source_path))
            self.assertEqual(
                imported.sha256,
                ProjectStore(root).load().payload["sources"][0]["sha256"],
            )

    def test_refuses_symlink_source_and_media_directory(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "study"
            initialize_project(root)
            real_source = parent / "recording.wav"
            real_source.write_bytes(b"audio")
            link = parent / "linked.wav"
            link.symlink_to(real_source)
            with self.assertRaisesRegex(MediaIngestError, "symlink"):
                ingest_media(root, link, create_restricted_rights=True)

            media = root / "media"
            replacement = parent / "replacement"
            replacement.mkdir()
            media.rename(root / "media-real")
            media.symlink_to(replacement, target_is_directory=True)
            with self.assertRaises(MediaIngestError):
                ingest_media(root, real_source, create_restricted_rights=True)
            self.assertEqual([], ProjectStore(root).load().payload["sources"])

    def test_duplicate_or_invalid_rights_roll_back_copied_media(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "study"
            initialize_project(root)
            source = parent / "take.flac"
            source.write_bytes(b"same recording")
            first = ingest_media(root, source, create_restricted_rights=True)
            before = sorted(item.name for item in (root / "media").iterdir())

            with self.assertRaises(MediaIngestError):
                ingest_media(root, source, create_restricted_rights=True)
            self.assertEqual(before, sorted(item.name for item in (root / "media").iterdir()))

            other = parent / "other.flac"
            other.write_bytes(b"other recording")
            with self.assertRaisesRegex(MediaIngestError, "rights_id"):
                ingest_media(root, other, rights_id="rights:does-not-exist")
            self.assertFalse(any("other" in item.name for item in (root / "media").iterdir()))
            self.assertEqual(
                first.source_id, ProjectStore(root).load().payload["sources"][0]["id"]
            )

    def test_requires_explicit_rights_choice_and_private_media_permissions(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "study"
            initialize_project(root)
            source = parent / "take.wav"
            source.write_bytes(b"audio")
            with self.assertRaises(MediaIngestError):
                ingest_media(root, source)
            os.chmod(root / "media", 0o755)
            with self.assertRaisesRegex(MediaIngestError, "owner-private"):
                ingest_media(root, source, create_restricted_rights=True)

    def test_rejects_empty_media_without_publishing_it(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "study"
            initialize_project(root)
            source = parent / "empty.wav"
            source.touch()

            with self.assertRaisesRegex(MediaIngestError, "must not be empty"):
                ingest_media(root, source, create_restricted_rights=True)

            self.assertEqual([], ProjectStore(root).load().payload["sources"])

    def test_rejects_media_over_the_finite_ingest_quota_before_copying(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "study"
            initialize_project(root)
            source = parent / "oversized.wav"
            source.touch()
            os.truncate(source, MAX_INGEST_BYTES + 1)

            with self.assertRaisesRegex(MediaIngestError, "exceeds"):
                ingest_media(root, source, create_restricted_rights=True)

            self.assertEqual([], ProjectStore(root).load().payload["sources"])
            self.assertEqual(
                ["README.txt"], sorted(item.name for item in (root / "media").iterdir())
            )

    def test_copy_verification_rejects_any_source_identity_or_timestamp_change(self) -> None:
        before = SimpleNamespace(
            st_dev=1,
            st_ino=2,
            st_size=3,
            st_mtime_ns=4,
            st_ctime_ns=5,
        )
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"):
            after = SimpleNamespace(**vars(before))
            setattr(after, field, getattr(after, field) + 1)
            self.assertFalse(_same_source_snapshot(before, after), field)


if __name__ == "__main__":
    unittest.main()
