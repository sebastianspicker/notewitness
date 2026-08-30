"""Feature API for durable browser-requested local processing."""

from ._processing_contracts import (
    MAX_STATUS_MESSAGE_CHARS,
    MAX_WORKBENCH_JOB_ATTEMPTS,
    MAX_WORKBENCH_JOBS,
    WORKBENCH_SHUTDOWN_WAIT_SECONDS,
    DisabledWorkbenchExecutor,
    WorkbenchExecutor,
    WorkbenchJob,
    WorkbenchJobKind,
    WorkbenchJobState,
    WorkbenchProcessingError,
)
from ._processing_lifecycle import WorkbenchProcessingService
from ._processing_store import WorkbenchJobStore


__all__ = [
    "DisabledWorkbenchExecutor",
    "MAX_STATUS_MESSAGE_CHARS",
    "MAX_WORKBENCH_JOB_ATTEMPTS",
    "MAX_WORKBENCH_JOBS",
    "WORKBENCH_SHUTDOWN_WAIT_SECONDS",
    "WorkbenchExecutor",
    "WorkbenchJob",
    "WorkbenchJobKind",
    "WorkbenchJobState",
    "WorkbenchJobStore",
    "WorkbenchProcessingError",
    "WorkbenchProcessingService",
]
