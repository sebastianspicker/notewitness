import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const build = spawnSync("node", [path.join(root, "scripts/build_pages_demo.mjs")], {
  cwd: root,
  encoding: "utf8",
});
assert.equal(build.status, 0, build.stderr || build.stdout);

const html = fs.readFileSync(path.join(root, "_site/index.html"), "utf8");
const client = fs.readFileSync(path.join(root, "_site/assets/pages-demo.js"), "utf8");
for (const expected of [
  "Static demo · synthetic fixture · no project data is loaded",
  "Read-only interface walkthrough",
  'data-demo-panel="review"',
  'data-demo-panel="transcript"',
  'data-demo-panel="lesson"',
  "synthetic-lesson.timeline",
  "data-accept=",
  "./assets/notewitness-mark.svg",
]) {
  assert.ok(html.includes(expected), `missing Pages demo contract: ${expected}`);
}
assert.ok(!html.includes('src="/assets/'));
assert.ok(!html.includes('href="/assets/'));
assert.ok(!client.includes("fetch("));
assert.ok(client.includes('dataset.demoCommand = "simulated"'));
assert.ok(fs.existsSync(path.join(root, "_site/.nojekyll")));

console.log("GitHub Pages demo contract verified");
