import assert from "node:assert/strict";
import { request } from "node:http";
import { after, before, test } from "node:test";
import { buildSite } from "../scripts/build.mjs";
import { startPreview } from "../scripts/serve.mjs";
import { testOutput } from "./helpers.mjs";

let server;
let baseUrl;
before(async () => {
  const result = await buildSite({ outputRoot: await testOutput() });
  server = await startPreview({ root: result.outputRoot, port: 0 });
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});
after(async () => {
  if (server) await new Promise((resolve) => server.close(resolve));
});

test("home and installation pages are readable without any backend", async () => {
  for (const route of ["/", "/downloads/"]) {
    const response = await fetch(`${baseUrl}${route}`);
    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type"), /text\/html/u);
    assert.match(await response.text(), /Aurum Pharma/u);
    assert.match(
      response.headers.get("content-security-policy"),
      /connect-src 'none'/u,
    );
    assert.equal(response.headers.get("set-cookie"), null);
  }
});

test("unknown installers and API paths cannot fall back to a successful HTML page", async () => {
  for (const route of [
    "/AurumPharma.msix",
    "/api/v1/auth/me",
    "/.env",
    "/missing-page",
  ]) {
    assert.equal((await fetch(`${baseUrl}${route}`)).status, 404);
  }
});

test("preview cannot mutate data and HEAD returns no body", async () => {
  assert.equal((await fetch(baseUrl, { method: "POST" })).status, 405);
  const response = await fetch(baseUrl, { method: "HEAD" });
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "");
});

test("preview refuses encoded traversal and malformed paths", async () => {
  for (const route of [
    "/..%2f..%2fAGENTS.md",
    "/%5c..%5cAGENTS.md",
    "/%2fdownloads",
    "/%00",
    "/%zz",
  ]) {
    const status = await new Promise((resolve, reject) => {
      const req = request(`${baseUrl}/`, { path: route }, (response) => {
        response.resume();
        resolve(response.statusCode);
      });
      req.on("error", reject);
      req.end();
    });
    assert.ok([400, 404].includes(status));
  }
});
