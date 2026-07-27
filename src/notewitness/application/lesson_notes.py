"""Deterministic local lesson-note projection over validated evidence."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Any, Iterable, Mapping

from notewitness.application.lesson_projection_events import project_events
from notewitness.application.lesson_projection_relations import project_relations
from notewitness.application.speaker_alignment import diarization_run_id
from notewitness.domain.lesson import (
    LessonNotes,
    LessonProgress,
    LessonStatistics,
    LessonSummary,
    NamedCount,
    PracticePlan,
    RecordRevisionLink,
    SourceReference,
    SourceGraphProvenance,
)
from notewitness.evidence import EvidenceGraph


class LessonNotesProjector:
    """Project graph claims and review state without inventing new facts."""

    @classmethod
    def project(cls, graph: EvidenceGraph) -> LessonNotes:
        graph.require_valid()
        project = graph.payload.get("project", {})
        actors = graph.index("actors")
        targets = graph.index("targets")
        events = graph.index("events")
        relations = graph.records("relations")

        event_projection = project_events(
            events,
            targets,
            actors,
            speaker_roles_by_event=_speaker_roles_by_event(events, relations),
        )
        relation_projection = project_relations(
            relations,
            events,
            targets,
            actors,
            event_projection.anchors_by_event,
            graph.records("revisions"),
        )
        relation_counts = Counter(str(relation["type"]) for relation in relations)
        practice_tasks = relation_projection.practice_tasks
        overview = (
            f"{len(event_projection.activity)} timed evidence events, "
            f"{len(relation_projection.moments)} pedagogical relations, and "
            f"{len(practice_tasks)} evidence-backed practice tasks."
        )
        canonical_payload = json.dumps(
            graph.payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        used_generator_ids = tuple(
            sorted(
                {
                    str(record["generator_id"])
                    for collection in ("events", "relations")
                    for record in graph.records(collection)
                }
            )
        )
        used_rights_ids = tuple(
            sorted(
                {
                    str(record["rights_id"])
                    for collection in ("sources", "events", "relations")
                    for record in graph.records(collection)
                }
            )
        )
        revisions_by_record: dict[str, list[str]] = {}
        for revision in graph.records("revisions"):
            revisions_by_record.setdefault(str(revision["record_id"]), []).append(
                str(revision["id"])
            )
        contains_remote = cls._contains_remote_derived_evidence(
            graph, used_generator_ids
        )

        return LessonNotes(
            project_id=str(project.get("id", "")),
            title=str(project.get("name", "Untitled lesson")),
            network_mode=graph.network_policy().mode.value,
            source_graph=SourceGraphProvenance(
                schema_version=str(graph.payload.get("schema_version", "")),
                canonical_sha256=hashlib.sha256(canonical_payload).hexdigest(),
                generator_ids=used_generator_ids,
                rights_ids=used_rights_ids,
                revisions=tuple(
                    RecordRevisionLink(record_id=record_id, revision_ids=tuple(ids))
                    for record_id, ids in sorted(revisions_by_record.items())
                ),
            ),
            sources=tuple(
                SourceReference(
                    source_id=str(source["id"]),
                    kind=str(source["kind"]),
                    uri=str(source["uri"]),
                    sha256=str(source["sha256"]),
                    rights_id=str(source["rights_id"]),
                )
                for source in sorted(
                    graph.records("sources"), key=lambda item: str(item["id"])
                )
            ),
            activity=event_projection.activity,
            transcript=event_projection.transcript,
            full_transcript=event_projection.full_transcript,
            transcript_suggestions=event_projection.transcript_suggestions,
            summary=LessonSummary(
                method="evidence_relation_projection_v1",
                overview=overview,
                topics=relation_projection.topics,
                feedback=relation_projection.feedback,
                key_moments=relation_projection.moments,
            ),
            relation_suggestions=relation_projection.suggestions,
            practice_tasks=practice_tasks,
            practice_plan=PracticePlan(
                method="explicit_task_relation_projection_v1",
                title="Practice plan: confirm with teacher or researcher",
                tasks=practice_tasks,
            ),
            bookmarks=event_projection.bookmarks,
            progress=LessonProgress(
                revised_attempt_relation_ids=relation_projection.revised_relation_ids
            ),
            statistics=LessonStatistics(
                timeline_duration_us=event_projection.timeline_duration_us,
                transcript_turn_count=len(event_projection.transcript),
                bookmark_count=len(event_projection.bookmarks),
                practice_task_count=len(practice_tasks),
                note_or_pitch_event_count=sum(
                    entry.content_kind in {"note", "pitch"}
                    for entry in event_projection.full_transcript
                ),
                unknown_actor_event_count=sum(
                    segment.actor_role == "unknown"
                    for segment in event_projection.activity
                ),
                activity=event_projection.activity_statistics,
                relations=tuple(
                    NamedCount(name=name, count=count)
                    for name, count in sorted(relation_counts.items())
                ),
                timeline_extents=event_projection.timeline_extents,
            ),
            limitations=(
                "This artifact projects the current evidence graph; it does not run "
                "ASR, diarization, or music transcription.",
                "Speaker roles, summaries, and tasks retain the source graph's review "
                "status and require human verification.",
                "Descriptive statistics are activity counts and durations, not grades "
                "or assessments of a learner, teacher, or performance.",
            ),
            contains_remote_derived_evidence=contains_remote,
        )

    @staticmethod
    def _contains_remote_derived_evidence(
        graph: EvidenceGraph, generator_ids: tuple[str, ...]
    ) -> bool | None:
        generators = graph.index("generators")
        locations: list[str | None] = []
        for generator_id in generator_ids:
            parameters = generators.get(generator_id, {}).get("parameters")
            location = (
                parameters.get("processing_location")
                if isinstance(parameters, dict)
                else None
            )
            locations.append(str(location) if location is not None else None)
        if "remote" in locations:
            return True
        if graph.network_policy().mode.value == "offline":
            return False
        if locations and all(location == "local" for location in locations):
            return False
        return None


def _speaker_roles_by_event(
    events: Mapping[str, Mapping[str, Any]],
    relations: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    event_positions = {event_id: index for index, event_id in enumerate(events)}
    candidates: dict[str, list[tuple[str, str, int, str]]] = {}
    for relation in relations:
        if (
            relation.get("type") != "local:speaker_alignment"
            or relation.get("review_status")
            not in {"machine_suggested", "human_accepted", "human_created"}
        ):
            continue
        speech_id: str | None = None
        speaker_id: str | None = None
        arguments = relation.get("arguments")
        if not isinstance(arguments, list):
            continue
        for argument in arguments:
            if not isinstance(argument, Mapping) or argument.get("ref_kind") != "event":
                continue
            ref_id = argument.get("ref_id")
            if not isinstance(ref_id, str):
                continue
            if argument.get("role") == "speech":
                speech_id = ref_id
            elif argument.get("role") == "speaker_segment":
                speaker_id = ref_id
        speaker = events.get(speaker_id or "")
        body = speaker.get("body") if isinstance(speaker, Mapping) else None
        value = body.get("value") if isinstance(body, Mapping) else None
        cluster = value.get("anonymous_cluster_id") if isinstance(value, Mapping) else None
        if speech_id in events and isinstance(cluster, str) and cluster:
            candidates.setdefault(speech_id, []).append(
                (
                    str(relation["review_status"]),
                    diarization_run_id(speaker),
                    event_positions.get(speaker_id or "", -1),
                    cluster,
                )
            )
    clusters: dict[str, set[str]] = {}
    for speech_id, values in candidates.items():
        human = tuple(
            item for item in values if item[0] in {"human_accepted", "human_created"}
        )
        selected = human
        if not selected:
            latest_run = max(values, key=lambda item: item[2])[1]
            selected = tuple(item for item in values if item[1] == latest_run)
        clusters[speech_id] = {item[3] for item in selected}
    for event_id, event in events.items():
        body = event.get("body")
        source_id = (
            body.get("source_suggestion_id")
            if isinstance(body, Mapping)
            else None
        )
        if isinstance(source_id, str) and source_id in clusters:
            clusters.setdefault(event_id, set()).update(clusters[source_id])
    return {
        event_id: (
            f"anonymous speaker {next(iter(values))}"
            if len(values) == 1
            else "anonymous speakers " + " / ".join(sorted(values))
        )
        for event_id, values in clusters.items()
    }
