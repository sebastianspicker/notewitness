from __future__ import annotations

import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from notewitness.projects.private_fs import private_directory
from notewitness.projects.private_sqlite import PrivateSQLiteFailure
from notewitness.workbench.jobs import (
    WorkbenchJobStore,
    WorkbenchProcessingError,
    _job_store_error,
)


class WorkbenchJobStoreTests(unittest.TestCase):
    def test_rejects_parent_replacement(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            runs = parent / "workbench-runs"
            runs.mkdir(mode=0o700)
            store = WorkbenchJobStore(runs / "jobs.sqlite")

            runs.rename(parent / "workbench-runs-displaced")
            runs.mkdir(mode=0o700)

            with self.assertRaisesRegex(
                WorkbenchProcessingError, "^job_store_parent_identity_changed$"
            ):
                store.list()

    def test_every_shared_failure_keeps_its_stable_code(self) -> None:
        expected = {
            PrivateSQLiteFailure.PARENT_NOT_PRIVATE: "job_store_parent_not_private",
            PrivateSQLiteFailure.PARENT_IDENTITY_CHANGED: "job_store_parent_identity_changed",
            PrivateSQLiteFailure.DATABASE_NOT_REGULAR: "job_store_not_regular",
            PrivateSQLiteFailure.DATABASE_NOT_PRIVATE: "job_store_not_private",
            PrivateSQLiteFailure.SIDECAR_NOT_REGULAR: "job_store_not_regular",
            PrivateSQLiteFailure.SIDECAR_NOT_PRIVATE: "job_store_not_private",
            PrivateSQLiteFailure.SIDECAR_ACCESS_FAILED: "job_store_sidecar_access_failed",
        }
        self.assertEqual(set(PrivateSQLiteFailure), set(expected))
        for failure, code in expected.items():
            with self.subTest(failure=failure):
                error = _job_store_error(failure)
                self.assertIs(WorkbenchProcessingError, type(error))
                self.assertEqual(code, str(error))

    def test_observable_store_failures_keep_their_codes(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            shared = parent / "shared"
            shared.mkdir()
            shared.chmod(0o755)
            with self.assertRaisesRegex(WorkbenchProcessingError, "^job_store_parent_not_private$"):
                WorkbenchJobStore(shared / "jobs.sqlite")

            runs = parent / "runs"
            runs.mkdir(mode=0o700)
            readable = runs / "readable.sqlite"
            readable.touch(mode=0o600)
            readable.chmod(0o644)
            with self.assertRaisesRegex(WorkbenchProcessingError, "^job_store_not_private$"):
                WorkbenchJobStore(readable)

            (runs / "directory.sqlite").mkdir(mode=0o700)
            with self.assertRaisesRegex(WorkbenchProcessingError, "^job_store_not_regular$"):
                WorkbenchJobStore(runs / "directory.sqlite")

    def test_close_failure_during_an_active_exception_still_reports_its_code(self) -> None:
        with TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            runs.mkdir(mode=0o700)
            store = WorkbenchJobStore(runs / "jobs.sqlite")
            real_close = os.close
            real_fstat = os.fstat

            def close_then_fail(descriptor: int) -> None:
                is_file = stat.S_ISREG(real_fstat(descriptor).st_mode)
                real_close(descriptor)
                if is_file:
                    raise OSError("close failure")

            with patch(
                "notewitness.projects.private_sqlite.os.close", side_effect=close_then_fail
            ):
                try:
                    raise KeyError("body failure")
                except KeyError:
                    with private_directory(runs) as parent, self.assertRaisesRegex(
                        WorkbenchProcessingError, "^job_store_sidecar_access_failed$"
                    ):
                        store._database.secure_sidecars(parent)


if __name__ == "__main__":
    unittest.main()
