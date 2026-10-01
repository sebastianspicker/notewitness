"""Bounded execution of explicitly discovered local media/model tools."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Callable, Mapping, Sequence

from notewitness.analysis.local_tools.contracts import (
    LocalToolCancelled,
    LocalToolResult,
)
from notewitness.analysis.local_tools.discovery import LocalTool
from notewitness.analysis.local_tools.policy import (
    bounded_environment as _bounded_environment,
    network_isolated_command as _network_isolated_command,
    resource_limited_launcher_command as _policy_resource_limited_launcher_command,
    validated_arguments as _validated_arguments,
    validated_private_directory as _validated_private_directory,
    validated_search_paths as _validated_search_paths,
    validated_timeout as _validated_timeout,
)
from notewitness.analysis.local_tools.process import (
    execute_bounded as _execute_bounded,
)


__all__ = ("BoundedLocalToolRunner",)


class BoundedLocalToolRunner:
    """Execute one fixed binary with finite resources and no shell expansion."""

    def __init__(
        self,
        tool: LocalTool,
        *,
        launcher_factory: Callable[[], Path] | None = None,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self.tool = tool
        self._launcher_factory = launcher_factory
        self._popen_factory = popen_factory

    def run(
        self,
        arguments: Sequence[str],
        *,
        working_directory: str | Path,
        timeout_seconds: int,
        deny_network: bool = True,
        environment: Mapping[str, str] | None = None,
        executable_search_paths: Sequence[str | Path] = (),
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> LocalToolResult:
        args = _validated_arguments(arguments)
        timeout = _validated_timeout(timeout_seconds)
        workdir = _validated_private_directory(Path(working_directory))
        search_paths = _validated_search_paths(executable_search_paths)
        environment = _bounded_environment(
            environment,
            workdir=workdir,
            tool_directory=self.tool.executable.parent,
            search_paths=search_paths,
        )
        command = (os.fspath(self.tool.executable), *args)
        network_isolated = deny_network
        if deny_network:
            command = _network_isolated_command(command)
        launcher_command = _policy_resource_limited_launcher_command(
            command,
            timeout,
            launcher_factory=self._launcher_factory,
        )
        if cancellation_requested is not None and not callable(cancellation_requested):
            raise ValueError("cancellation_requested must be callable or None.")
        if cancellation_requested is not None and cancellation_requested():
            raise LocalToolCancelled(
                f"Local tool {self.tool.name!r} was cancelled before execution."
            )
        return _execute_bounded(
            tool=self.tool,
            launcher_command=launcher_command,
            workdir=workdir,
            environment=environment,
            timeout_seconds=timeout,
            network_isolated=network_isolated,
            cancellation_requested=cancellation_requested,
            popen_factory=self._popen_factory,
        )
