from __future__ import annotations

import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from notewitness.projects.private_fs import private_directory
from notewitness.projects.private_sqlite import (
    PrivateSQLiteDatabase,
    PrivateSQLiteFailure,
    busy_timeout_pragma,
    secure_sidecar,
    validated_busy_timeout_ms,
)


class _TranslatedError(RuntimeError):
    def __init__(self, failure: PrivateSQLiteFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


def _database(path: Path, **options: object) -> PrivateSQLiteDatabase:
    return PrivateSQLiteDatabase(
        path, errors=_TranslatedError, busy_timeout_ms=5_000, **options  # type: ignore[arg-type]
    )


def _private_root(temporary: str) -> Path:
    root = Path(temporary) / "private"
    root.mkdir(mode=0o700)
    return root


def _close_then_fail(failures: list[int]):
    real_close = os.close
    real_fstat = os.fstat

    def close(descriptor: int) -> None:
        is_file = stat.S_ISREG(real_fstat(descriptor).st_mode)
        real_close(descriptor)
        if is_file:
            failures.append(descriptor)
            raise OSError("close failure")

    return close


class PrivateSQLiteLifecycleTests(unittest.TestCase):
    def test_creates_owner_private_database_and_round_trips_transactions(self) -> None:
        with TemporaryDirectory() as temporary:
            database = _database(_private_root(temporary) / "jobs.sqlite")
            with database.transaction() as connection:
                connection.execute("CREATE TABLE items (value TEXT)")
                connection.execute("INSERT INTO items VALUES ('kept')")
            with self.assertRaises(KeyError):
                with database.transaction() as connection:
                    connection.execute("INSERT INTO items VALUES ('rolled back')")
                    raise KeyError("abort")
            with database.connection() as connection:
                rows = connection.execute("SELECT value FROM items").fetchall()

            self.assertEqual([("kept",)], [tuple(row) for row in rows])
            self.assertEqual(0o600, database.path.stat().st_mode & 0o777)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{database.path}{suffix}")
                if sidecar.exists():
                    self.assertEqual(0o600, sidecar.stat().st_mode & 0o777)

    def test_leading_pragmas_run_before_shared_pragmas(self) -> None:
        connection = MagicMock()
        with TemporaryDirectory() as temporary, patch(
            "notewitness.projects.private_sqlite.sqlite3.connect", return_value=connection
        ) as connect:
            database = PrivateSQLiteDatabase(
                _private_root(temporary) / "jobs.sqlite",
                errors=_TranslatedError,
                busy_timeout_ms=12_345,
                leading_pragmas=("PRAGMA foreign_keys = ON",),
            )
            with database.connection():
                pass

        self.assertEqual(12.345, connect.call_args.kwargs["timeout"])
        self.assertIsNone(connect.call_args.kwargs["isolation_level"])
        self.assertEqual(
            [
                "PRAGMA foreign_keys = ON",
                "PRAGMA busy_timeout = 12345",
                "PRAGMA journal_mode = WAL",
                "PRAGMA synchronous = FULL",
            ],
            [call.args[0] for call in connection.execute.call_args_list],
        )
        connection.close.assert_called_once_with()

    def test_busy_timeout_is_a_bounded_integer(self) -> None:
        self.assertEqual("PRAGMA busy_timeout = 1", busy_timeout_pragma(1))
        self.assertEqual(60_000, validated_busy_timeout_ms(60_000))
        for value in (True, "5000", 1.5, 0, 60_001):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "^busy_timeout_ms must be between 1 and 60000\\.$"
            ):
                validated_busy_timeout_ms(value)


class PrivateSQLiteFailureTranslationTests(unittest.TestCase):
    def assertFailure(self, failure: PrivateSQLiteFailure, caught: _TranslatedError) -> None:
        self.assertIs(failure, caught.failure)

    def test_parent_not_private(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary) / "shared"
            parent.mkdir(mode=0o700)
            parent.chmod(0o755)
            with self.assertRaises(_TranslatedError) as caught:
                _database(parent / "jobs.sqlite")
            self.assertFailure(PrivateSQLiteFailure.PARENT_NOT_PRIVATE, caught.exception)

    def test_parent_identity_changed(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _private_root(temporary)
            database = _database(root / "jobs.sqlite")
            root.rename(Path(temporary) / "displaced")
            root.mkdir(mode=0o700)
            with self.assertRaises(_TranslatedError) as caught:
                with database.connection():
                    pass
            self.assertFailure(PrivateSQLiteFailure.PARENT_IDENTITY_CHANGED, caught.exception)

    def test_database_not_regular_or_symlink(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _private_root(temporary)
            (root / "directory.sqlite").mkdir(mode=0o700)
            target = root / "target.sqlite"
            target.touch(mode=0o600)
            (root / "link.sqlite").symlink_to(target)
            for name in ("directory.sqlite", "link.sqlite"):
                with self.subTest(name=name), self.assertRaises(_TranslatedError) as caught:
                    with _database(root / name).connection():
                        pass
                self.assertFailure(PrivateSQLiteFailure.DATABASE_NOT_REGULAR, caught.exception)

    def test_database_not_private(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _private_root(temporary)
            database = root / "jobs.sqlite"
            database.touch(mode=0o600)
            database.chmod(0o644)
            with self.assertRaises(_TranslatedError) as caught:
                with _database(database).connection():
                    pass
            self.assertFailure(PrivateSQLiteFailure.DATABASE_NOT_PRIVATE, caught.exception)
            self.assertEqual(0o644, database.stat().st_mode & 0o777)


class PrivateSQLiteSidecarTests(unittest.TestCase):
    def _secure(self, path: Path, *, defer: bool = False) -> None:
        with private_directory(path.parent) as parent:
            secure_sidecar(
                parent,
                path.name,
                _TranslatedError,
                defer_close_failure_to_active_exception=defer,
            )

    def _sidecar(self, temporary: str) -> Path:
        sidecar = _private_root(temporary) / "jobs.sqlite-wal"
        sidecar.write_bytes(b"sidecar")
        sidecar.chmod(0o644)
        return sidecar

    def test_absent_sidecar_is_ignored(self) -> None:
        with TemporaryDirectory() as temporary:
            self._secure(_private_root(temporary) / "jobs.sqlite-wal")

    def test_regular_sidecar_is_privatized(self) -> None:
        with TemporaryDirectory() as temporary:
            sidecar = self._sidecar(temporary)
            self._secure(sidecar)
            self.assertEqual(0o600, sidecar.stat().st_mode & 0o777)

    def test_symlink_sidecar_is_not_regular_and_target_is_untouched(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _private_root(temporary)
            target = root / "unrelated"
            target.write_bytes(b"must not be chmodded")
            target.chmod(0o644)
            sidecar = root / "jobs.sqlite-wal"
            sidecar.symlink_to(target)
            with self.assertRaises(_TranslatedError) as caught:
                self._secure(sidecar)
            self.assertIs(PrivateSQLiteFailure.SIDECAR_NOT_REGULAR, caught.exception.failure)
            self.assertEqual(0o644, target.stat().st_mode & 0o777)

    def test_fifo_sidecar_is_not_regular(self) -> None:
        with TemporaryDirectory() as temporary:
            sidecar = _private_root(temporary) / "jobs.sqlite-wal"
            os.mkfifo(sidecar, 0o600)
            with self.assertRaises(_TranslatedError) as caught:
                self._secure(sidecar)
            self.assertIs(PrivateSQLiteFailure.SIDECAR_NOT_REGULAR, caught.exception.failure)

    def test_foreign_owned_sidecar_is_not_private_and_not_chmodded(self) -> None:
        with TemporaryDirectory() as temporary:
            sidecar = self._sidecar(temporary)
            real_fstat = os.fstat

            def foreign_owner(descriptor: int) -> os.stat_result:
                metadata = real_fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    return metadata
                values = list(metadata)
                values[stat.ST_UID] = os.getuid() + 1
                return os.stat_result(values)

            with patch("notewitness.projects.private_sqlite.os.fstat", side_effect=foreign_owner):
                with self.assertRaises(_TranslatedError) as caught:
                    self._secure(sidecar)
            self.assertIs(PrivateSQLiteFailure.SIDECAR_NOT_PRIVATE, caught.exception.failure)
            self.assertEqual(0o644, sidecar.stat().st_mode & 0o777)

    def test_open_os_error_is_access_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            sidecar = self._sidecar(temporary)
            real_open = os.open

            def deny(path: object, flags: int, mode: int = 0o777, **kwargs: object) -> int:
                if os.fspath(path) == sidecar.name:
                    raise PermissionError("private details must not escape")
                return real_open(path, flags, mode, **kwargs)

            with patch("notewitness.projects.private_sqlite.os.open", side_effect=deny):
                with self.assertRaises(_TranslatedError) as caught:
                    self._secure(sidecar)
            self.assertIs(PrivateSQLiteFailure.SIDECAR_ACCESS_FAILED, caught.exception.failure)

    def test_close_failure_never_replaces_own_failure_in_either_mode(self) -> None:
        for defer in (False, True):
            with self.subTest(defer=defer), TemporaryDirectory() as temporary:
                sidecar = self._sidecar(temporary)
                closes: list[int] = []
                with (
                    patch(
                        "notewitness.projects.private_sqlite.os.fchmod",
                        side_effect=OSError("chmod failure"),
                    ),
                    patch(
                        "notewitness.projects.private_sqlite.os.close",
                        side_effect=_close_then_fail(closes),
                    ),
                    self.assertRaises(_TranslatedError) as caught,
                ):
                    self._secure(sidecar, defer=defer)
                self.assertIs(PrivateSQLiteFailure.SIDECAR_ACCESS_FAILED, caught.exception.failure)
                self.assertEqual("chmod failure", str(caught.exception.__cause__))
                self.assertEqual(1, len(closes))

    def test_close_failure_replaces_success_in_either_mode(self) -> None:
        for defer in (False, True):
            with self.subTest(defer=defer), TemporaryDirectory() as temporary:
                sidecar = self._sidecar(temporary)
                with (
                    patch(
                        "notewitness.projects.private_sqlite.os.close",
                        side_effect=_close_then_fail([]),
                    ),
                    self.assertRaises(_TranslatedError) as caught,
                ):
                    self._secure(sidecar, defer=defer)
                self.assertIs(PrivateSQLiteFailure.SIDECAR_ACCESS_FAILED, caught.exception.failure)
                self.assertEqual("close failure", str(caught.exception.__cause__))

    def test_close_failure_during_an_active_exception_depends_on_policy(self) -> None:
        for defer in (False, True):
            with self.subTest(defer=defer), TemporaryDirectory() as temporary:
                sidecar = self._sidecar(temporary)
                with patch(
                    "notewitness.projects.private_sqlite.os.close",
                    side_effect=_close_then_fail([]),
                ):
                    try:
                        raise KeyError("body failure")
                    except KeyError:
                        if defer:
                            self._secure(sidecar, defer=True)
                        else:
                            with self.assertRaises(_TranslatedError) as caught:
                                self._secure(sidecar, defer=False)
                            self.assertIs(
                                PrivateSQLiteFailure.SIDECAR_ACCESS_FAILED,
                                caught.exception.failure,
                            )
                self.assertEqual(0o600, sidecar.stat().st_mode & 0o777)

    def test_base_exception_closes_descriptor_and_close_policy_applies(self) -> None:
        class SentinelFailure(BaseException):
            pass

        for defer in (False, True):
            with self.subTest(defer=defer), TemporaryDirectory() as temporary:
                sidecar = self._sidecar(temporary)
                sentinel = SentinelFailure()
                descriptors: list[int] = []
                closes: list[int] = []

                def fail_fchmod(descriptor: int, mode: int) -> None:
                    descriptors.append(descriptor)
                    raise sentinel

                with (
                    patch(
                        "notewitness.projects.private_sqlite.os.fchmod",
                        side_effect=fail_fchmod,
                    ),
                    patch(
                        "notewitness.projects.private_sqlite.os.close",
                        side_effect=_close_then_fail(closes),
                    ),
                    self.assertRaises(BaseException) as caught,
                ):
                    self._secure(sidecar, defer=defer)

                if defer:
                    self.assertIs(sentinel, caught.exception)
                else:
                    # Without deferral only this module's own failures suppress
                    # a close failure; a foreign BaseException is replaced.
                    self.assertIsInstance(caught.exception, _TranslatedError)
                    self.assertIs(sentinel, caught.exception.__context__.__context__)
                self.assertEqual(descriptors, closes)
                with self.assertRaises(OSError):
                    os.fstat(descriptors[0])


if __name__ == "__main__":
    unittest.main()
