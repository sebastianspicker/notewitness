"""Feature entry point for the dependency-free local workbench HTTP runtime."""

from __future__ import annotations

import json
from pathlib import Path
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
            print(json.dumps({"network_mode": "loopback_only", "url": url}))
        else:
            print(
                json.dumps(
                    {
                        "launch_url": launch_url,
                        "network_mode": "loopback_only",
                        "notice": "Browser launch failed; this single-use URL grants access to the private workbench.",
                    }
                )
            )
    else:
        print(
            json.dumps(
                {
                    "launch_url": server.launch_url,
                    "network_mode": "loopback_only",
                    "notice": "This single-use URL grants access to the private workbench.",
                }
            )
        )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = [
    "LocalWorkbenchServer",
    "WorkbenchRequestHandler",
    "WorkbenchServerError",
    "serve_workbench",
]
