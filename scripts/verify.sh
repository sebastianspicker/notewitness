#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(dirname -- "$script_dir")
cd "$repo_dir"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$repo_dir/src"

python3 -m json.tool schemas/v0.1/evidence-graph.schema.json >/dev/null
python3 -m json.tool schemas/v0.1/context.jsonld >/dev/null
python3 -m json.tool examples/synthetic-lesson/project.json >/dev/null
python3 -m json.tool docs/workbench-runtime.example.json >/dev/null
python3 scripts/verify_public_hygiene.py
python3 -m notewitness validate examples/synthetic-lesson/project.json
python3 -m notewitness inspect examples/synthetic-lesson/project.json >/dev/null
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
while IFS= read -r module; do
  node --check "$module"
done < <(find src/notewitness/workbench/assets -type f \( -name '*.js' -o -name '*.mjs' \) | sort)
node --check scripts/render_pages_demo.mjs
node --check scripts/pages_demo_client.js
python3 -m py_compile scripts/assemble_pages_demo.py
python3 -m py_compile scripts/assemble_pages_tour.py
python3 -m py_compile scripts/build_demo_state.py
pages_site_dir=$(mktemp -d "${TMPDIR:-/tmp}/notewitness-pages.XXXXXX")
trap 'rm -rf "$pages_site_dir"' EXIT
bash scripts/build_pages_demo.sh "$pages_site_dir"
