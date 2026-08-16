/** Request boundary contract checks for the local workbench API helper. */
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const apiModule = path.resolve(
  here,
  "../../src/notewitness/presentation/workbench_assets/js/api.mjs",
);
const { createApi } = await import(pathToFileURL(apiModule).href);

const originalFetch = globalThis.fetch;
const calls = [];
globalThis.fetch = async (requestPath, options) => {
  calls.push({ requestPath, options });
  return {
    ok: true,
    status: 200,
    json: async () => ({ accepted: true }),
  };
};

try {
  const api = createApi({ state: {} });
  assert.deepEqual(await api.request("/api/jobs", { method: "GET" }), { accepted: true });
  assert.deepEqual(calls, [{
    requestPath: "/api/jobs",
    options: { credentials: "same-origin", method: "GET" },
  }]);

  for (const path of [
    "http://example.test/api/jobs",
    "https://example.test/api/jobs",
    "//example.test/api/jobs",
    "/api\\..\\assets",
    "/api/%2f%2fexample.test",
    "/api/%5C..%5Cassets",
    "/api/../assets",
    "/assets/workbench",
  ]) {
    await assert.rejects(api.request(path), {
      name: "TypeError",
      message: "request path must be a same-origin relative /api/... path.",
    });
  }
  assert.equal(calls.length, 1, "rejected paths must not reach fetch");
} finally {
  globalThis.fetch = originalFetch;
}

console.log("workbench API request boundary verified");
