"""Compatibility façade for the dependency-free local workbench HTTP runtime."""

from __future__ import annotations

import json
from pathlib import Path
import webbrowser

from notewitness.application.music_export import (
    MusicExportError,
    MusicExportFormat,
    SymbolicMusicExportService,
)
from notewitness.application.transcript_export import (
    TranscriptEvidenceExportService,
    TranscriptEvidenceLayer,
    TranscriptExportError,
)
from notewitness.application.transcript_review_service import add_project_actor
from notewitness.application.workbench import (
    WorkbenchError,
    accept_evidence_suggestion,
    accept_relation_suggestion,
    capture_publication_hook,
    create_exact_time_bookmark,
    project_workbench,
    reject_relation_suggestion,
    resolve_media_source,
    revise_evidence_annotation,
    set_practice_task_completed,
)
from notewitness.application.workbench_local_executor import LocalWorkbenchExecutor
from notewitness.application.workbench_processing import (
    WorkbenchExecutor,
    WorkbenchProcessingError,
    WorkbenchProcessingService,
)
from notewitness.domain.transcription_options import TranscriptExportFormat
from notewitness.domain.utilities import MetronomePlan, tuner_reading
from notewitness.local_artifacts import LocalArtifactError
from notewitness.media_ingest import MAX_INGEST_BYTES, MediaIngestError, ingest_media
from notewitness.project_store import ProjectConflictError, ProjectStore, ProjectStoreError

from .workbench_http import (
    LocalWorkbenchServer,
    WorkbenchRequestHandler,
    _POST_ERRORS,
    _POST_ERROR_RESPONSES,
    _require_assets,
)
from .workbench_media import (
    MAX_CAPTURE_BYTES,
    _STREAM_CHUNK_BYTES,
    _compatible_suffixes,
    _is_mp4_header,
    _parse_range,
    _require_verified_media,
    _safe_import_suffix,
    _stat_identity,
    _stream_request,
    _validate_capture_container,
    _validate_import_container,
)
from .workbench_protocol import (
    MAX_JSON_REQUEST_BYTES,
    MAX_REQUEST_PATH_CHARS,
    WorkbenchServerError,
    _coarse_log_route,
    _optional_number,
    _optional_string,
    _required_header,
    _required_integer,
    _required_number,
    _required_string,
    _unique_object,
)
from .workbench_protocol import (
    _ALLOWED_BIND_HOST,
    _ASSETS,
    _CSS_TYPE,
    _JS_TYPE,
    _LAUNCH_PATH_PREFIX,
    _SESSION_COOKIE_NAME,
)
from .workbench_media import _CAPTURE_SUFFIXES, _IMPORT_NAME_SUFFIXES, _IMPORT_SUFFIXES


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
