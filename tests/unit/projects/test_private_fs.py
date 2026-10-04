from __future__ import annotations

import errno
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from notewitness.projects.private_fs import (
    PrivatePathError,
    _directory_has_extended_acl,
    content_stat_identity,
    open_directory_no_follow,
    open_private_directory,
    trusted_absolute_path,
)


_VAR_IS_SYSTEM_ALIAS = Path("/var").is_symlink() and Path(
    os.path.realpath("/var")
) == Path("/private/var")


class TrustedAbsolutePathTests(unittest.TestCase):
    def test_relative_paths_become_lexically_absolute(self) -> None:
        self.assertEqual(
            Path(os.path.abspath("some/child/../file")),
            trusted_absolute_path("some/child/../file"),
        )

    def test_paths_outside_var_are_not_resolved(self) -> None:
        with TemporaryDirectory() as temporary:
            root = trusted_absolute_path(temporary)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            link.symlink_to(real)

            self.assertEqual(link / "x", trusted_absolute_path(link / "x"))

    def test_var_prefix_lookalike_is_unchanged(self) -> None:
        self.assertEqual(Path("/variable/x"), trusted_absolute_path(Path("/variable/x")))

    @unittest.skipUnless(_VAR_IS_SYSTEM_ALIAS, "/var is not the macOS /private/var alias")
    def test_macos_var_alias_is_canonicalized(self) -> None:
        self.assertEqual(Path("/private/var"), trusted_absolute_path("/var"))
        self.assertEqual(
            Path("/private/var/folders/x"),
            trusted_absolute_path(Path("/var/folders/x")),
        )

    @unittest.skipIf(_VAR_IS_SYSTEM_ALIAS, "/var is the macOS /private/var alias")
    def test_var_is_unchanged_without_the_system_alias(self) -> None:
        self.assertEqual(Path("/var/x"), trusted_absolute_path("/var/x"))


class OpenDirectoryNoFollowTests(unittest.TestCase):
    def test_returns_descriptor_for_the_exact_directory(self) -> None:
        with TemporaryDirectory() as temporary:
            target = trusted_absolute_path(Path(temporary) / "a" / "b")
            target.mkdir(parents=True)
            descriptor = open_directory_no_follow(target)
            try:
                opened = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            expected = target.stat()
            self.assertEqual((expected.st_dev, expected.st_ino), (opened.st_dev, opened.st_ino))

    def test_symlink_component_raises_os_error(self) -> None:
        with TemporaryDirectory() as temporary:
            root = trusted_absolute_path(temporary)
            (root / "real").mkdir()
            (root / "link").symlink_to(root / "real")
            with self.assertRaises(OSError) as caught:
                open_directory_no_follow(root / "link")
            self.assertIn(caught.exception.errno, {errno.ELOOP, errno.ENOTDIR})

    def test_missing_or_file_component_raises_os_error(self) -> None:
        with TemporaryDirectory() as temporary:
            root = trusted_absolute_path(temporary)
            (root / "file").write_bytes(b"")
            with self.assertRaises(OSError) as missing:
                open_directory_no_follow(root / "missing")
            self.assertEqual(errno.ENOENT, missing.exception.errno)
            with self.assertRaises(OSError) as not_directory:
                open_directory_no_follow(root / "file")
            self.assertEqual(errno.ENOTDIR, not_directory.exception.errno)

    def test_relative_path_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            open_directory_no_follow(Path("relative"))


class TrustedAncestorTests(unittest.TestCase):
    def test_rejects_peer_writable_nonsticky_ancestor(self) -> None:
        with TemporaryDirectory() as temporary:
            shared = trusted_absolute_path(Path(temporary) / "shared")
            shared.mkdir(mode=0o700)
            private = shared / "private"
            private.mkdir(mode=0o700)
            shared.chmod(0o777)

            with self.assertRaisesRegex(PrivatePathError, "replaceable ancestor"):
                open_private_directory(private, require_trusted_ancestors=True)

    def test_accepts_trusted_child_below_sticky_ancestor(self) -> None:
        with TemporaryDirectory() as temporary:
            shared = trusted_absolute_path(Path(temporary) / "shared")
            shared.mkdir(mode=0o700)
            private = shared / "private"
            private.mkdir(mode=0o700)
            shared.chmod(0o1777)

            capability = open_private_directory(
                private, require_trusted_ancestors=True
            )
            capability.close()

    def test_rechecks_ancestor_permissions(self) -> None:
        with TemporaryDirectory() as temporary:
            shared = trusted_absolute_path(Path(temporary) / "shared")
            shared.mkdir(mode=0o700)
            private = shared / "private"
            private.mkdir(mode=0o700)
            capability = open_private_directory(
                private, require_trusted_ancestors=True
            )
            try:
                shared.chmod(0o777)
                with self.assertRaisesRegex(PrivatePathError, "replaceable ancestor"):
                    capability.require_current()
            finally:
                capability.close()

    def test_rejects_extended_acl_authority(self) -> None:
        with TemporaryDirectory() as temporary, patch(
            "notewitness.projects.private_fs._directory_has_extended_acl",
            return_value=True,
        ):
            private = trusted_absolute_path(Path(temporary))
            with self.assertRaisesRegex(PrivatePathError, "ACL-controlled"):
                open_private_directory(private, require_trusted_ancestors=True)

    @unittest.skipUnless(sys.platform == "darwin", "Darwin extended ACL test")
    def test_detects_native_allow_acl_but_ignores_deny_only_home_acl(self) -> None:
        home_descriptor = os.open(Path.home(), os.O_RDONLY | os.O_DIRECTORY)
        try:
            self.assertFalse(_directory_has_extended_acl(home_descriptor))
        finally:
            os.close(home_descriptor)

        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "acl"
            directory.mkdir()
            subprocess.run(
                [
                    "chmod",
                    "+a",
                    "everyone allow add_subdirectory,delete_child",
                    os.fspath(directory),
                ],
                check=True,
                capture_output=True,
            )
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assertTrue(_directory_has_extended_acl(descriptor))
            finally:
                os.close(descriptor)


class ContentStatIdentityTests(unittest.TestCase):
    def test_identity_tracks_inode_size_and_change_times(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "file"
            path.write_bytes(b"one")
            metadata = path.stat()
            self.assertEqual(
                (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                ),
                content_stat_identity(metadata),
            )


if __name__ == "__main__":
    unittest.main()
