from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from notewitness.local_artifacts import (
    LocalArtifactError,
    write_new_private_bytes,
    write_new_private_json,
)


class LocalArtifactTests(unittest.TestCase):
    def test_exact_bytes_are_private_exclusive_and_bounded(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            target = root / "raw.bin"

            write_new_private_bytes(target, b"\x00exact\xff", maximum_bytes=16)

            self.assertEqual(b"\x00exact\xff", target.read_bytes())
            self.assertEqual(0o600, target.stat().st_mode & 0o777)
            with self.assertRaises(LocalArtifactError):
                write_new_private_bytes(target, b"replacement")
            with self.assertRaisesRegex(LocalArtifactError, "exceeds"):
                write_new_private_bytes(root / "large.bin", b"123", maximum_bytes=2)

    def test_write_refuses_symlinked_parent_component(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            real_parent = root / "real"
            nested = real_parent / "nested"
            nested.mkdir(parents=True)
            linked_parent = root / "linked"
            linked_parent.symlink_to(real_parent, target_is_directory=True)

            with self.assertRaises(LocalArtifactError):
                write_new_private_json(
                    linked_parent / "nested" / "notes.json", {"private": True}
                )

            self.assertFalse((nested / "notes.json").exists())

    def test_write_refuses_world_accessible_parent(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            public_parent = root / "public"
            public_parent.mkdir(mode=0o755)
            public_parent.chmod(0o755)

            with self.assertRaisesRegex(LocalArtifactError, "deny group"):
                write_new_private_json(public_parent / "notes.json", {"private": True})


if __name__ == "__main__":
    unittest.main()
