#!/usr/bin/env python3
"""Build deterministic workbench state for the public mock-data demo."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import sys
from pathlib import Path

from notewitness.lessons.actors import is_human_evidence_author
from notewitness.lessons.lesson_notes import LessonNotesProjector
from notewitness.evidence import EvidenceGraph
from notewitness.workbench.timeline import TimelineViewModel

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "synthetic-lesson" / "project.json"


MOCK_QUEUE = (
    (
        "event:mock-csharp-release",
        "event:instruction",
        "speech",
        "Release the C♯ at the end of bar 18; keep the bow moving.",
        0.86,
    ),
    (
        "event:mock-bars-18-21",
        "event:demonstration",
        "music",
        "Violin demonstration for bars 18–21.",
        0.79,
    ),
    (
        "event:mock-listen-release",
        "event:feedback",
        "speech",
        "Listen for the release before repeating bar 21.",
        0.74,
    ),
    (
        "event:mock-repeat-release",
        "event:attempt-2",
        "note",
        "Compare the next repetition with the C♯ release.",
        0.71,
    ),
)


def main() -> int:
    graph = EvidenceGraph.load(EXAMPLE)
    graph.require_valid()
    notes = LessonNotesProjector.project(graph)
    timeline = TimelineViewModel.from_lesson_notes(notes)
    payload = graph.payload
    project = payload["project"]
    actors = [
        {
            key: actor[key]
            for key in ("id", "role", "instrument_role")
            if key in actor
        }
        for actor in sorted(payload["actors"], key=lambda item: str(item["id"]))
    ]
    for actor in actors:
        actor["human_evidence_eligible"] = is_human_evidence_author(actor)

    duration_us = max(
        (extent.end_us for extent in notes.statistics.timeline_extents),
        default=30_000_000,
    )
    # The example has no project media; expose a non-playable display row so the
    # single-source rail and timeline share one synthetic source identity.
    media = [
        {
            "display_name": "mock-violin-lesson.timeline",
            "duration_us": duration_us,
            "kind": "synthetic_timeline",
            "source_id": "source:synthetic-script",
            "url": "",
        }
    ]
    lesson = notes.as_dict()
    entries = {entry["event_id"]: entry for entry in lesson["full_transcript"]}
    suggestions = []
    for event_id, source_event_id, content_kind, text, confidence in MOCK_QUEUE:
        suggestion = deepcopy(entries[source_event_id])
        suggestion.update(
            {
                "event_id": event_id,
                "content_kind": content_kind,
                "display_text": text,
                "body_value": text,
                "review_status": "machine_suggested",
                "layer": "mock_input",
                "generator_id": "generator:mock-ledger",
                "rights_id": "rights:mock-data",
                "confidence": {"kind": "adapter_reported", "value": confidence},
            }
        )
        suggestions.append(suggestion)
    lesson["transcript_suggestions"] = suggestions
    lesson["title"] = "Mock violin lesson · bars 18–21"

    snapshot = {
        "actors": actors,
        "capabilities": {
            "bookmark": False,
            "capture": False,
            "metronome": False,
            "music_export": False,
            "playback": False,
            "review": True,
            "tuner": False,
        },
        "lesson": lesson,
        "media": media,
        "metronome": {
            "bars": 1,
            "beats_per_bar": 4,
            "bpm": 72,
            "subdivisions": 1,
        },
        "project": {
            "id": str(project.get("id", "")),
            "network_mode": notes.network_mode,
            "saved": True,
            "sha256": "mock-data-only",
            "title": "Mock violin lesson · bars 18–21",
        },
        "timeline": asdict(timeline),
        "source_id": "source:synthetic-script",
        "duration_us": duration_us,
    }
    json.dump(snapshot, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
