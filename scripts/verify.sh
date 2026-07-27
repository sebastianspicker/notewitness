#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(dirname -- "$script_dir")
cd "$repo_dir"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$repo_dir/src"

python3 -m json.tool schemas/v0.1/evidence-graph.schema.json >/dev/null
python3 -m json.tool schemas/v0.1/context.jsonld >/dev/null
python3 -m json.tool fixtures/synthetic_lesson/project.json >/dev/null
python3 -m json.tool docs/workbench-runtime.example.json >/dev/null
python3 scripts/verify_public_hygiene.py
python3 -m unittest discover -s tests -t . -v
python3 -m notewitness validate fixtures/synthetic_lesson/project.json
python3 -m notewitness inspect fixtures/synthetic_lesson/project.json >/dev/null
python3 -m notewitness capabilities >/dev/null
python3 -m notewitness --version >/dev/null
doctor_status=0
python3 -m notewitness doctor --profile tonic-local >/dev/null || doctor_status=$?
test "$doctor_status" -eq 6
doctor_status=0
python3 -m notewitness doctor --profile notewitness-v0.1 >/dev/null || doctor_status=$?
test "$doctor_status" -eq 6
doctor_status=0
python3 -m notewitness doctor --profile noscribe-research >/dev/null || doctor_status=$?
test "$doctor_status" -eq 6
runtime_status=0
python3 -m notewitness runtime-doctor >/dev/null || runtime_status=$?
test "$runtime_status" -eq 6
python3 -m notewitness tuner-reading 440 >/dev/null
python3 -m notewitness metronome-plan --bpm 120 --bars 1 >/dev/null
python3 -m notewitness transcription-plan \
  --job-id job:verify \
  --source-id source:verify \
  --duration-us 1000000 \
  --model-profile profile:precise >/dev/null
node --check src/notewitness/presentation/workbench_assets/app.js
node --check src/notewitness/presentation/workbench_assets/workbench_ui.mjs
node --check src/notewitness/presentation/workbench_assets/ui/utils.mjs
node --check src/notewitness/presentation/workbench_assets/ui/shell.mjs
node --check src/notewitness/presentation/workbench_assets/ui/timeline.mjs
node --check src/notewitness/presentation/workbench_assets/ui/panels.mjs
node --check src/notewitness/presentation/workbench_assets/ui/processing.mjs
node --check src/notewitness/presentation/workbench_assets/ui/context.mjs
node --check src/notewitness/presentation/workbench_assets/ui/transport.mjs
node --check src/notewitness/presentation/workbench_assets/js/api.mjs
node --check src/notewitness/presentation/workbench_assets/js/playback.mjs
node --check src/notewitness/presentation/workbench_assets/js/processing.mjs
node --check src/notewitness/presentation/workbench_assets/js/actions.mjs
node tests/javascript/test_tuner.mjs
node tests/javascript/test_workbench_ui.mjs
