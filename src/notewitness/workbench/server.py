"""Feature entry point for the dependency-free local workbench HTTP runtime."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
import json
import os
from pathlib import Path
import select
import sys
import threading
import time
from typing import TextIO
import webbrowser

from .http import LocalWorkbenchServer, WorkbenchRequestHandler
from .protocol import WorkbenchServerError


def serve_workbench(
    project_root: str | Path,
    *,
    port: int = 0,
    open_browser: bool = True,
    runtime_config_path: str | Path | None = None,
) -> None:
    """Serve one project until interrupted, never binding beyond loopback."""

    server = LocalWorkbenchServer(project_root, port, runtime_config_path=runtime_config_path)
    url = f"{server.origin}/"
    if open_browser:
        launch_url = server.launch_url
        if webbrowser.open(launch_url, new=2, autoraise=True):
            print(json.dumps({"network_mode": "loopback_only", "url": url}), flush=True)
        else:
            print(
                json.dumps(
                    {
                        "launch_url": launch_url,
                        "network_mode": "loopback_only",
                        "notice": "Browser launch failed; this single-use URL grants access to the private workbench.",
                    }
                ),
                flush=True,
            )
    else:
        print(
            json.dumps(
                {
                    "launch_url": server.launch_url,
                    "network_mode": "loopback_only",
                    "notice": "This single-use URL grants access to the private workbench.",
                }
            ),
            flush=True,
        )
    try:
        _start_relaunch_listener(server, sys.stdin)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _start_relaunch_listener(server: LocalWorkbenchServer, stdin: TextIO | None) -> bool:
    """Let an interactive operator press Enter for a new single-use launch URL."""

    if stdin is None or not stdin.isatty():
        return False
    try:
        foreground = os.tcgetpgrp(stdin.fileno()) == os.getpgrp()
    except OSError:
        return False  # Not this process's controlling terminal; input never arrives.
    if foreground:
        print(
            "Press Enter here to print a new single-use launch URL.",
            file=sys.stderr,
            flush=True,
        )
    threading.Thread(
        target=_print_relaunch_urls,
        args=(_foreground_terminal_lines(stdin.fileno()), server),
        name="notewitness-relaunch",
        daemon=True,
    ).start()
    return True


def _foreground_terminal_lines(descriptor: int) -> Iterator[bytes]:
    """Yield terminal input, reading only while this process is in the foreground.

    A background process that reads its terminal is stopped by SIGTTIN, which
    would suspend the server after ``&`` or ``bg``; polling avoids that read.
    """

    while True:
        try:
            ready, _, _ = select.select([descriptor], [], [], 0.5)
            if not ready:
                continue
            if os.tcgetpgrp(descriptor) != os.getpgrp():
                time.sleep(0.5)
                continue
            chunk = os.read(descriptor, 4096)
        except OSError:
            return
        if not chunk:
            return
        yield chunk


def _print_relaunch_urls(lines: Iterable[object], server: LocalWorkbenchServer) -> None:
    for _line in lines:
        try:
            _print_launch_notice(server.issue_launch_url())
        except OSError:
            return


def _print_launch_notice(launch_url: str) -> None:
    print(
        json.dumps(
            {
                "launch_url": launch_url,
                "network_mode": "loopback_only",
                "notice": "This new single-use URL grants access to the private workbench; earlier unused launch URLs no longer work.",
            }
        ),
        flush=True,
    )


__all__ = [
    "LocalWorkbenchServer",
    "WorkbenchRequestHandler",
    "WorkbenchServerError",
    "serve_workbench",
]
