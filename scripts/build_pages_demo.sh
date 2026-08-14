#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(dirname -- "$script_dir")
asset_source="$repo_dir/src/notewitness/presentation/workbench_assets"

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
cp "$asset_source/notewitness-mark.svg" "$site_dir/assets/notewitness-mark.svg"
sed 's#"/assets/styles/#"./styles/#g' "$asset_source/app.css" > "$site_dir/assets/app.css"
cp "$script_dir/pages_demo_client.js" "$site_dir/assets/pages-demo.js"

PYTHONPATH="$repo_dir/src" python3 "$script_dir/export_screenshot_state.py" \
  | node "$script_dir/render_pages_demo.mjs" \
  | python3 "$script_dir/assemble_pages_demo.py" \
  | sed 's#/assets/#./assets/#g' > "$site_dir/index.html"
touch "$site_dir/.nojekyll"

grep -Fq "Static demo · synthetic fixture" "$site_dir/index.html"
grep -Fq 'data-demo-panel="review"' "$site_dir/index.html"
grep -Fq 'data-demo-panel="transcript"' "$site_dir/index.html"
grep -Fq 'data-demo-panel="lesson"' "$site_dir/index.html"
grep -Fq "synthetic-lesson.timeline" "$site_dir/index.html"
grep -Fq "data-accept=" "$site_dir/index.html"
if grep -Eq '(src|href)="/assets/' "$site_dir/index.html"; then
  echo "Pages demo contains a root-absolute asset URL" >&2
  exit 1
fi
if grep -Fq "fetch(" "$site_dir/assets/pages-demo.js"; then
  echo "Pages demo client must not contain network requests" >&2
  exit 1
fi
if grep -RIEq --exclude='notewitness-mark.svg' \
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

echo "built and verified static Pages artifact from the real workbench renderer and synthetic fixture"
