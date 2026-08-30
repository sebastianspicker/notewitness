from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from notewitness.workbench._processing_store import (
    WorkbenchJobStore,
    WorkbenchProcessingError,
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

            with self.assertRaises(WorkbenchProcessingError):
                store.list()


if __name__ == "__main__":
    unittest.main()
