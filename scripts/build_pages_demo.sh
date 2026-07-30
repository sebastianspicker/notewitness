#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(dirname -- "$script_dir")
site_dir="$repo_dir/_site"
asset_source="$repo_dir/src/notewitness/presentation/workbench_assets"

rm -rf "$site_dir"
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

echo "built and verified _site from the real workbench renderer and synthetic fixture"
