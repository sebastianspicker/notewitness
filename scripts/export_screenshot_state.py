#!/usr/bin/env python3
"""Export deterministic workbench JSON for public screenshot captures."""

from __future__ import annotations

from dataclasses import asdict
import json
import sys
from pathlib import Path

from notewitness.application.actor_eligibility import is_human_evidence_author
from notewitness.application.lesson_notes import LessonNotesProjector
from notewitness.evidence import EvidenceGraph
from notewitness.presentation.timeline import TimelineViewModel

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "synthetic_lesson" / "project.json"


def main() -> int:
    graph = EvidenceGraph.load(FIXTURE)
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
    # Fixture has no project media; expose a non-playable display row so the
    # single-source rail and timeline share one synthetic source identity.
    media = [
        {
            "display_name": "synthetic-lesson.timeline",
            "duration_us": duration_us,
            "kind": "synthetic_timeline",
            "source_id": "source:synthetic-script",
            "url": "",
        }
    ]
    lesson = notes.as_dict()
    # Deterministic review-boundary queue: demote one human speech line to a
    # machine suggestion without mutating the fixture on disk.
    suggestions = []
    for entry in lesson["full_transcript"]:
        if entry.get("content_kind") == "speech" and entry.get("actor_role") == "teacher":
            suggestion = dict(entry)
            suggestion["event_id"] = "event:screenshot-suggestion"
            suggestion["review_status"] = "machine_suggested"
            suggestion["confidence"] = {
                "kind": "adapter_reported",
                "value": 0.82,
            }
            suggestions.append(suggestion)
            break
    lesson["transcript_suggestions"] = suggestions

    snapshot = {
        "actors": actors,
        "capabilities": {
            "bookmark": True,
            "capture": True,
            "metronome": True,
            "music_export": True,
            "playback": False,
            "review": True,
            "tuner": True,
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
            "sha256": "screenshot-synthetic",
            "title": str(project.get("name", "Untitled lesson")),
        },
        "timeline": asdict(timeline),
        "source_id": "source:synthetic-script",
        "duration_us": duration_us,
    }
    json.dump(snapshot, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
