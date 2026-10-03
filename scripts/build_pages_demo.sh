#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(dirname -- "$script_dir")
asset_source="$repo_dir/src/notewitness/workbench/assets"

if test "$#" -ne 1; then
  echo "usage: scripts/build_pages_demo.sh OUTPUT_DIRECTORY" >&2
  exit 2
fi

site_dir=$1
case "$site_dir" in
  ""|/|"$repo_dir")
    echo "refusing unsafe Pages output directory: $site_dir" >&2
    exit 2
    ;;
esac
if test -e "$site_dir" && test ! -d "$site_dir"; then
  echo "Pages output path exists and is not a directory: $site_dir" >&2
  exit 2
fi
if test -d "$site_dir" && test -n "$(find "$site_dir" -mindepth 1 -print -quit)"; then
  echo "Pages output directory must be empty: $site_dir" >&2
  exit 2
fi

mkdir -p "$site_dir/assets"
cp -R "$asset_source/styles" "$site_dir/assets/styles"
mkdir -p "$site_dir/assets/fonts"
cp "$asset_source"/fonts/*.woff2 "$asset_source"/fonts/OFL-*.txt "$site_dir/assets/fonts/"
cp "$asset_source/notewitness-mark.svg" "$site_dir/assets/notewitness-mark.svg"
sed 's#"/assets/styles/#"./styles/#g' "$asset_source/app.css" > "$site_dir/assets/app.css"
cp "$script_dir/pages_demo_client.js" "$site_dir/assets/pages-demo.js"

screenshot_source="$repo_dir/docs/screenshots"
if test ! -d "$screenshot_source"; then
  echo "missing docs/screenshots for the Pages tour: $screenshot_source" >&2
  exit 1
fi
mkdir -p "$site_dir/assets/screenshots"
cp "$screenshot_source"/*.png "$site_dir/assets/screenshots/"

PYTHONPATH="$repo_dir/src" python3 "$script_dir/build_demo_state.py" \
  | node "$script_dir/render_pages_demo.mjs" \
  | python3 "$script_dir/assemble_pages_demo.py" \
  | sed 's#/assets/#./assets/#g' > "$site_dir/index.html"

python3 "$script_dir/assemble_pages_tour.py" > "$site_dir/tour.html"
touch "$site_dir/.nojekyll"

source_revision=${GITHUB_SHA:-$(git -C "$repo_dir" rev-parse HEAD)}
case "$source_revision" in
  ""|*[!0-9A-Fa-f]*)
    echo "Pages source revision must be a Git commit SHA" >&2
    exit 1
    ;;
esac
dirty=false
if test -n "$(git -C "$repo_dir" status --porcelain --untracked-files=normal)"; then
  dirty=true
fi
fixture_sha256=$(shasum -a 256 "$repo_dir/examples/synthetic-lesson/project.json" | awk '{print $1}')
{
  printf '{\n'
  printf '  "schema_version": "1",\n'
  printf '  "source_revision": "%s",\n' "$source_revision"
  printf '  "dirty": %s,\n' "$dirty"
  printf '  "synthetic_fixture_sha256": "%s",\n' "$fixture_sha256"
  printf '  "artifact_sha256": {\n'
  printf '    "index.html": "%s",\n' "$(shasum -a 256 "$site_dir/index.html" | awk '{print $1}')"
  printf '    "tour.html": "%s"' "$(shasum -a 256 "$site_dir/tour.html" | awk '{print $1}')"
  while IFS= read -r asset; do
    relative=${asset#"$site_dir/"}
    printf ',\n    "%s": "%s"' "$relative" "$(shasum -a 256 "$asset" | awk '{print $1}')"
  done < <(find "$site_dir/assets" -type f -print | LC_ALL=C sort)
  printf '\n  }\n}\n'
} > "$site_dir/release.json"

grep -Fq 'data-demo-mode="mock"' "$site_dir/index.html"
grep -Fq "Mock lesson · browser-only" "$site_dir/index.html"
grep -Fq "Changes reset on reload" "$site_dir/index.html"
grep -Fq "default-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'" "$site_dir/index.html"
grep -Fq 'data-demo-panel="review"' "$site_dir/index.html"
grep -Fq 'data-demo-panel="transcript"' "$site_dir/index.html"
grep -Fq 'data-demo-panel="lesson"' "$site_dir/index.html"
grep -Fq "data-accept=" "$site_dir/index.html"
grep -Fq "data-reject=" "$site_dir/index.html"
grep -Fq "data-revise=" "$site_dir/index.html"
grep -Fq "data-context-inspector" "$site_dir/index.html"
grep -Fq "applyMockDecision" "$site_dir/assets/pages-demo.js"
grep -Fq "tour.html" "$site_dir/assets/pages-demo.js"
grep -Fq "Screenshot tour" "$site_dir/tour.html"
grep -Fq 'href="./index.html"' "$site_dir/tour.html"
grep -Fq 'src="./assets/screenshots/' "$site_dir/tour.html"
screenshot_count=$(find "$site_dir/assets/screenshots" -type f -name '*.png' | wc -l | tr -d ' ')
if test "$screenshot_count" -lt 4; then
  echo "Pages tour must include at least four screenshots" >&2
  exit 1
fi
python3 -m json.tool "$site_dir/release.json" >/dev/null
grep -Fq '"synthetic_fixture_sha256"' "$site_dir/release.json"
review_cards=$(grep -o 'data-review-card=' "$site_dir/index.html" | wc -l | tr -d ' ')
if test "$review_cards" -lt 3; then
  echo "Pages demo must render at least three mock review records" >&2
  exit 1
fi
if grep -Eq '(src|href)="/assets/' "$site_dir/index.html"; then
  echo "Pages demo contains a root-absolute asset URL" >&2
  exit 1
fi
if grep -RIEq '[s]imulated|walkthrough|data-demo-command' "$site_dir"; then
  echo "Pages demo contains retired presentation framing" >&2
  exit 1
fi
if grep -RIEq '\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|mediaDevices)\b' "$site_dir"; then
  echo "Pages demo client must not contain browser network or device APIs" >&2
  exit 1
fi
if grep -RIEq '\b(localStorage|sessionStorage|indexedDB|document\.cookie)\b' "$site_dir"; then
  echo "Pages demo client must not persist browser-session state" >&2
  exit 1
fi
if grep -RIEq --exclude='notewitness-mark.svg' --exclude='OFL-*.txt' \
  '(/[U]sers/|/[h]ome/|[A-Za-z]:[/\\][U]sers[/\\]|-----BEGIN ([A-Z0-9 ]+ )?PRIVATE KEY-----|\b(github_pat_|gh[pousr]_|(sk|rk)-(proj-)?)|file://|https?://)' \
  "$site_dir"; then
  echo "Pages demo contains a private path, credential shape, or network URL" >&2
  exit 1
fi
if find "$site_dir" -type f \( \
  -iname '*.aac' -o -iname '*.aif' -o -iname '*.aiff' -o -iname '*.caf' \
  -o -iname '*.db' -o -iname '*.flac' -o -iname '*.key' -o -iname '*.m4a' \
  -o -iname '*.mid' -o -iname '*.midi' -o -iname '*.mov' -o -iname '*.mp3' \
  -o -iname '*.mp4' -o -iname '*.ogg' -o -iname '*.opus' -o -iname '*.p12' \
  -o -iname '*.pem' -o -iname '*.pfx' -o -iname '*.sqlite' -o -iname '*.sqlite3' \
  -o -iname '*.wav' -o -iname '*.webm' \
  \) -print -quit | grep -q .; then
  echo "Pages demo contains a private or media artifact" >&2
  exit 1
fi

echo "built and verified browser-only mock Pages artifact from the workbench renderer"
