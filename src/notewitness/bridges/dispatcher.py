"""Select the maintained local provider for an installed bridge entry point."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from ._protocol import BridgeError, MAX_BYTES, fail


def main() -> None:
    try:
        argv = sys.argv[1:]
        if argv != ["--request", "request.json"]:
            raise BridgeError("expected exactly: --request request.json")
        raw = Path("request.json").read_bytes()
        if len(raw) > MAX_BYTES:
            raise BridgeError("request.json exceeds 2 MiB")
        payload = json.loads(raw.decode("utf-8"))
        stage = payload.get("stage") if isinstance(payload, dict) else None
        if stage == "note_transcription":
            from .basic_pitch_bridge import run
        elif stage == "activity_segmentation":
            from .panns_activity_bridge import run
        elif stage == "anonymous_diarization":
            from .pyannote_bridge import run
        elif stage in {"instrument_detection", "instrument_diarization"}:
            from .panns_instrument_bridge import run
        else:
            raise BridgeError("no maintained default bridge supports this stage")
        run(argv)
    except BridgeError as exc:
        fail(exc)
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError):
        fail(BridgeError("local provider bridge could not run"))


if __name__ == "__main__":
    main()
