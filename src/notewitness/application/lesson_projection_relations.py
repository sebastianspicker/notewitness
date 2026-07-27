"""Project graph relations into evidence-backed summaries and tasks."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from notewitness.application.lesson_projection_events import (
    anchor_from_target,
    is_accepted_record,
    unique_anchors,
)
from notewitness.domain.lesson import (
    EvidenceExcerpt,
    EvidenceTopic,
    PedagogicalMoment,
    PracticeTask,
    text_body,
)
from notewitness.domain.timeline import EvidenceAnchor


@dataclass(frozen=True, slots=True)
class RelationProjection:
    moments: tuple[PedagogicalMoment, ...]
    suggestions: tuple[PedagogicalMoment, ...]
    feedback: tuple[EvidenceExcerpt, ...]
    practice_tasks: tuple[PracticeTask, ...]
    topics: tuple[EvidenceTopic, ...]
    revised_relation_ids: tuple[str, ...]


def project_relations(
    relations: Iterable[Mapping[str, Any]],
    events: Mapping[str, Mapping[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
    actors: Mapping[str, Mapping[str, Any]],
    anchors_by_event: Mapping[str, tuple[EvidenceAnchor, ...]],
    revisions: Iterable[Mapping[str, Any]] = (),
) -> RelationProjection:
    relation_records = tuple(relations)
    reviewed_suggestion_ids = {
        str(revision["record_id"])
        for revision in revisions
        if revision.get("operation") in {"supersede", "reject"}
        and isinstance(revision.get("record_id"), str)
    }
    accepted_relations = tuple(
        relation for relation in relation_records if is_accepted_record(relation)
    )
    other_relations = tuple(
        relation
        for relation in relation_records
        if not is_accepted_record(relation)
        and str(relation["id"]) not in reviewed_suggestion_ids
    )
    moments = tuple(
        sorted(
            (
                relation_moment(relation, events, targets, anchors_by_event)
                for relation in accepted_relations
            ),
            key=moment_sort_key,
        )
    )
    return RelationProjection(
        moments=moments,
        suggestions=tuple(
            sorted(
                (
                    relation_moment(relation, events, targets, anchors_by_event)
                    for relation in other_relations
                ),
                key=moment_sort_key,
            )
        ),
        feedback=feedback_excerpts(
            accepted_relations, events, actors, anchors_by_event
        ),
        practice_tasks=practice_tasks(
            accepted_relations, events, anchors_by_event
        ),
        topics=score_topics(targets.values()),
        revised_relation_ids=tuple(
            str(relation["id"])
            for relation in accepted_relations
            if relation.get("type") in {"repeats", "revises"}
        ),
    )


def relation_moment(
    relation: Mapping[str, Any],
    events: Mapping[str, Mapping[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
    anchors_by_event: Mapping[str, tuple[EvidenceAnchor, ...]],
) -> PedagogicalMoment:
    event_ids: list[str] = []
    target_ids: list[str] = []
    labels: list[str] = []
    anchors: list[EvidenceAnchor] = []
    for argument in relation.get("arguments", []):
        if not isinstance(argument, Mapping):
            continue
        ref_id = str(argument.get("ref_id", ""))
        if argument.get("ref_kind") == "event" and ref_id in events:
            event_ids.append(ref_id)
            labels.append(text_body(events[ref_id]) or ref_id)
            anchors.extend(anchors_by_event[ref_id])
        elif argument.get("ref_kind") == "target" and ref_id in targets:
            target_ids.append(ref_id)
            anchors.append(anchor_from_target(targets[ref_id]))
    relation_type = str(relation["type"])
    confidence = relation.get("confidence")
    detail = " → ".join(labels) if labels else "linked evidence"
    return PedagogicalMoment(
        relation_id=str(relation["id"]),
        relation_type=relation_type,
        label=f"{relation_type.replace('_', ' ')}: {detail}",
        event_ids=tuple(dict.fromkeys(event_ids)),
        target_ids=tuple(dict.fromkeys(target_ids)),
        anchors=unique_anchors(anchors),
        review_status=str(relation["review_status"]),
        layer=str(relation["layer"]),
        generator_id=str(relation["generator_id"]),
        annotator_id=str(relation["annotator_id"]),
        rights_id=str(relation["rights_id"]),
        confidence=dict(confidence) if isinstance(confidence, Mapping) else {},
    )


def moment_sort_key(moment: PedagogicalMoment) -> tuple[int, str]:
    start = min(
        (anchor.span.start_us for anchor in moment.anchors), default=2**63 - 1
    )
    return (start, moment.relation_id)


def event_ids_for_role(
    relations: Iterable[Mapping[str, Any]], relation_type: str, role: str
) -> tuple[str, ...]:
    ids: list[str] = []
    for relation in relations:
        if relation.get("type") != relation_type:
            continue
        for argument in relation.get("arguments", []):
            if (
                isinstance(argument, Mapping)
                and argument.get("ref_kind") == "event"
                and argument.get("role") == role
            ):
                ids.append(str(argument["ref_id"]))
    return tuple(dict.fromkeys(ids))


def feedback_excerpts(
    relations: Iterable[Mapping[str, Any]],
    events: Mapping[str, Mapping[str, Any]],
    actors: Mapping[str, Mapping[str, Any]],
    anchors_by_event: Mapping[str, tuple[EvidenceAnchor, ...]],
) -> tuple[EvidenceExcerpt, ...]:
    feedback_ids = event_ids_for_role(relations, "feedback_on", "feedback")
    excerpts = []
    for event_id in feedback_ids:
        event = events[event_id]
        actor_id = str(event["actor_id"])
        excerpts.append(
            EvidenceExcerpt(
                event_id=event_id,
                text=text_body(event),
                actor_role=str(actors.get(actor_id, {}).get("role", "unknown")),
                anchors=anchors_by_event[event_id],
                review_status=str(event["review_status"]),
                layer=str(event["layer"]),
                generator_id=str(event["generator_id"]),
                rights_id=str(event["rights_id"]),
                confidence=(
                    dict(event["confidence"])
                    if isinstance(event.get("confidence"), Mapping)
                    else {}
                ),
            )
        )
    return tuple(excerpts)


def practice_tasks(
    relations: Iterable[Mapping[str, Any]],
    events: Mapping[str, Mapping[str, Any]],
    anchors_by_event: Mapping[str, tuple[EvidenceAnchor, ...]],
) -> tuple[PracticeTask, ...]:
    tasks: list[PracticeTask] = []
    completion_state = _practice_completion_state(events.values())
    for relation in relations:
        if relation.get("type") != "local:assigned_for_practice":
            continue
        assignment_ids = event_ids_for_role((relation,), relation["type"], "assignment")
        for event_id in assignment_ids:
            event = events[event_id]
            if not is_accepted_record(event):
                continue
            tasks.append(
                PracticeTask(
                    task_id=f"practice:{relation['id']}:{event_id}",
                    text=text_body(event) or event_id,
                    source_event_ids=(event_id,),
                    source_relation_ids=(str(relation["id"]),),
                    anchors=anchors_by_event[event_id],
                    review_status=str(event["review_status"]),
                    layer=str(event["layer"]),
                    generator_ids=tuple(
                        dict.fromkeys(
                            (
                                str(event["generator_id"]),
                                str(relation["generator_id"]),
                            )
                        )
                    ),
                    rights_ids=tuple(
                        dict.fromkeys(
                            (
                                str(event["rights_id"]),
                                str(relation["rights_id"]),
                            )
                        )
                    ),
                    completed=completion_state.get(
                        f"practice:{relation['id']}:{event_id}",
                        False,
                    ),
                )
            )
    return tuple(tasks)


def _practice_completion_state(
    events: Iterable[Mapping[str, Any]],
) -> dict[str, bool]:
    state: dict[str, bool] = {}
    for event in events:
        if event.get("type") != "local:practice_completion":
            continue
        body = event.get("body")
        value = body.get("value") if isinstance(body, Mapping) else None
        if not isinstance(value, Mapping):
            continue
        task_id = value.get("task_id")
        completed = value.get("completed")
        if isinstance(task_id, str) and isinstance(completed, bool):
            state[task_id] = completed
    return state


def score_topics(
    targets: Iterable[Mapping[str, Any]],
) -> tuple[EvidenceTopic, ...]:
    target_ids_by_label: dict[str, list[str]] = defaultdict(list)
    for target in targets:
        selector = target.get("musical_selector")
        if not isinstance(selector, Mapping) or selector.get("kind") != "score_span":
            continue
        score_id = str(selector.get("score_id", "score"))
        start = selector.get("bar_start")
        end = selector.get("bar_end")
        if start is None or end is None:
            label = f"Score span in {score_id}"
        elif start == end:
            label = f"{score_id}, bar {start}"
        else:
            label = f"{score_id}, bars {start}–{end}"
        target_ids_by_label[label].append(str(target["id"]))
    return tuple(
        EvidenceTopic(label=label, target_ids=tuple(target_ids))
        for label, target_ids in sorted(target_ids_by_label.items())
    )
