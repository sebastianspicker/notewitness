"""Compatibility facade for local workbench projections and mutations."""

from notewitness.application._workbench_contracts import (
    WorkbenchError,
    WorkbenchMutation,
)
from notewitness.application._workbench_projection import (
    capture_publication_hook,
    project_workbench,
    resolve_media_source,
)
from notewitness.application._workbench_review import (
    accept_evidence_suggestion,
    accept_relation_suggestion,
    create_exact_time_bookmark,
    reject_relation_suggestion,
    revise_evidence_annotation,
    set_practice_task_completed,
)

__all__ = (
    "WorkbenchError",
    "WorkbenchMutation",
    "accept_evidence_suggestion",
    "accept_relation_suggestion",
    "capture_publication_hook",
    "create_exact_time_bookmark",
    "project_workbench",
    "reject_relation_suggestion",
    "resolve_media_source",
    "revise_evidence_annotation",
    "set_practice_task_completed",
)

# Keep introspection and pickle paths at the long-standing public facade.
for _public_name in __all__:
    _public_object = globals()[_public_name]
    _public_object.__module__ = __name__
del _public_name, _public_object
