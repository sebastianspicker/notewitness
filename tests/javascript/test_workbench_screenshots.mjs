import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const script = path.resolve(here, "../../scripts/capture_workbench_screenshots.mjs");
const {
  assertExistingPathWithin,
  assertRegularOutputFile,
  captureFilePath,
  createCaptureWorkspace,
  expandCss,
  removeCaptureWorkspace,
  resolveExecutable,
  writeCaptureHtml,
} = await import(pathToFileURL(script).href);

const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), "notewitness-screenshot-test-"));
try {
  const output = path.join(sandbox, "docs/screenshots");
  fs.mkdirSync(output, { recursive: true });
  assert.equal(assertExistingPathWithin(sandbox, output, "output"), fs.realpathSync(output));
  assert.throws(() => assertExistingPathWithin(sandbox, path.join(sandbox, "../escape"), "output"), /escapes/);

  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "notewitness-screenshot-outside-"));
  try {
    const linked = path.join(sandbox, "linked");
    fs.symlinkSync(outside, linked, "dir");
    assert.throws(() => assertExistingPathWithin(sandbox, linked, "output"), /resolves outside/);
    assert.throws(() => createCaptureWorkspace(linked, sandbox), /resolves outside/);
  } finally {
    fs.rmSync(outside, { recursive: true, force: true });
  }

  const workspace = createCaptureWorkspace(output, sandbox);
  try {
    assert.ok(captureFilePath(workspace, "workbench-overview", "html").startsWith(`${workspace}${path.sep}`));
    assert.throws(() => captureFilePath(workspace, "../escape", "html"), /Unknown screenshot capture/);
    assert.throws(() => captureFilePath(workspace, "fixture", "png"), /Unknown screenshot capture/);
    const htmlPath = writeCaptureHtml(workspace, "review-boundary", "<main>trusted fixture</main>", "body { color: black; }");
    assert.match(fs.readFileSync(htmlPath, "utf8"), /trusted fixture/);
    assert.throws(() => writeCaptureHtml(workspace, "review-boundary", "<main>second write</main>", ""), /EEXIST/);
  } finally {
    removeCaptureWorkspace(workspace, output);
  }
  assert.ok(!fs.existsSync(workspace));

  const assets = path.join(sandbox, "assets");
  fs.mkdirSync(path.join(assets, "styles"), { recursive: true });
  fs.writeFileSync(path.join(assets, "app.css"), '@import url("/assets/styles/safe.css");\n');
  fs.writeFileSync(path.join(assets, "styles/safe.css"), "body { color: green; }\n");
  assert.match(expandCss(path.join(assets, "app.css"), assets), /color: green/);
  const outsideCss = path.join(sandbox, "outside.css");
  fs.writeFileSync(outsideCss, "body { color: red; }\n");
  fs.symlinkSync(outsideCss, path.join(assets, "styles/linked.css"));
  fs.writeFileSync(path.join(assets, "app.css"), '@import url("/assets/styles/linked.css");\n');
  assert.throws(() => expandCss(path.join(assets, "app.css"), assets), /resolves outside/);

  const executable = path.join(sandbox, "browser");
  fs.writeFileSync(executable, "#!/bin/sh\nexit 0\n", { mode: 0o700 });
  assert.equal(resolveExecutable(executable, "browser"), fs.realpathSync(executable));
  fs.chmodSync(executable, 0o600);
  assert.throws(() => resolveExecutable(executable, "browser"), /executable file/);

  const outputFile = path.join(output, "workbench-overview.png");
  fs.symlinkSync(outsideCss, outputFile);
  assert.throws(() => assertRegularOutputFile(outputFile), /not a regular file/);
} finally {
  fs.rmSync(sandbox, { recursive: true, force: true });
}

console.log("workbench screenshot boundary verified");
