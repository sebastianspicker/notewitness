from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from notewitness.projects.initialize import initialize_project
from notewitness.workbench.executor import (
    LocalWorkbenchExecutor,
    WorkbenchRuntimeConfigurationError,
)


class RuntimeConfigurationContractTests(unittest.TestCase):
    def test_versions_one_and_two_remain_recognized(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            project = root / "project"
            initialize_project(project)
            for version in (1, 2):
                with self.subTest(version=version):
                    config = root / f"runtime-v{version}.json"
                    config.write_text(json.dumps({"version": version}), encoding="utf-8")
                    config.chmod(0o600)
                    with self.assertRaisesRegex(
                        WorkbenchRuntimeConfigurationError,
                        "runtime_config_has_no_engines",
                    ):
                        LocalWorkbenchExecutor.from_private_config(project, config)

    def test_unknown_version_fails_before_engine_configuration(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            project = root / "project"
            initialize_project(project)
            config = root / "runtime-v3.json"
            config.write_text(json.dumps({"version": 3}), encoding="utf-8")
            config.chmod(0o600)
            with self.assertRaisesRegex(
                WorkbenchRuntimeConfigurationError,
                "runtime_config_version_unsupported",
            ):
                LocalWorkbenchExecutor.from_private_config(project, config)


if __name__ == "__main__":
    unittest.main()
