import assert from "node:assert/strict";
import { readFile, writeFile, symlink } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import { test } from "node:test";
import {
  buildSite,
  escapeAttribute,
  resolveAppUrl,
  websiteRoot,
} from "../scripts/build.mjs";
import { testOutput } from "./helpers.mjs";

test("local preview points to the existing app login; production has no guessed domain", () => {
  assert.equal(resolveAppUrl(), "http://localhost:5173/login");
  assert.equal(
    resolveAppUrl("https://app.example.test", true),
    "https://app.example.test/login",
  );
  assert.throws(() => resolveAppUrl(undefined, true), /explicit/u);
});

test("login configuration rejects credentials, redirect parameters and active URL schemes", () => {
  for (const input of [
    "javascript:alert(1)",
    "//app.example.test",
    "https://user:secret@app.example.test",
    "https://app.example.test/login?redirect=https://other.test",
    "https://app.example.test/#token",
    "https://app.example.test/other",
    "https://app.example.test\\@other.test",
    "https://app.example.test/\nlogin",
  ])
    assert.throws(() => resolveAppUrl(input, true), /APP_URL/u);
  assert.equal(escapeAttribute("\"<>&'"), "&quot;&lt;&gt;&amp;&#39;");
});

test("production does not publish local or cleartext application links", () => {
  for (const input of [
    "http://app.example.test",
    "https://localhost",
    "https://localhost.",
    "https://127.0.0.1",
    "https://[::1]",
  ]) {
    assert.throws(() => resolveAppUrl(input, true), /HTTPS/u);
  }
});

test("public pages build independently with correct links, local assets and no auth runtime", async () => {
  const result = await buildSite({
    appUrl: "https://app.example.test/login",
    production: true,
    outputRoot: await testOutput(),
  });
  const files = new Set(result.files);
  for (const page of result.files.filter((file) => file.endsWith(".html"))) {
    const html = await readFile(path.join(result.outputRoot, page), "utf8");
    assert.match(html, /<html[^>]+lang="ru"/u);
    assert.equal(
      (html.match(/<h1[\s>]/gu) ?? []).length,
      1,
      `${page}: one primary heading`,
    );
    assert.match(html, /https:\/\/app\.example\.test\/login/u);
    assert.doesNotMatch(
      html,
      /\{\{|localhost|<style[\s>]|\son\w+=|<form[\s>]|serviceWorker|localStorage|sessionStorage/u,
    );
    const links = [...html.matchAll(/(?:href|src)="([^"]+)"/gu)].map(
      (match) => match[1],
    );
    for (const link of links) {
      if (link === result.appUrl) continue;
      assert.ok(
        !/^(?:[a-z]+:)?\/\//iu.test(link),
        `${page}: unexpected external resource`,
      );
      const [pathname, fragment] = link.split("#");
      const target = pathname
        ? pathname.replace(/^\//u, "") +
          (pathname.endsWith("/") ? "index.html" : "")
        : page;
      assert.ok(files.has(target), `${page}: broken resource ${link}`);
      if (fragment) {
        const targetHtml = await readFile(
          path.join(result.outputRoot, target),
          "utf8",
        );
        assert.ok(
          targetHtml.includes(`id="${fragment}"`),
          `${page}: missing anchor ${fragment}`,
        );
      }
    }
  }
});

test("build cannot overwrite sources or write outside the repository", async () => {
  await assert.rejects(
    buildSite({ outputRoot: path.join(websiteRoot, "src") }),
    /source/u,
  );
  await assert.rejects(
    buildSite({ outputRoot: path.resolve(websiteRoot, "../..", "unrelated") }),
    /repository/u,
  );
  await assert.rejects(
    buildSite({ outputRoot: path.resolve(websiteRoot, "../frontend") }),
    /restricted/u,
  );
  if (process.platform === "win32")
    await assert.rejects(
      buildSite({ outputRoot: path.join(websiteRoot, "SRC") }),
      /source/u,
    );
});

test("rebuild removes only unchanged obsolete generated files and preserves unrelated files", async () => {
  const outputRoot = await testOutput();
  await buildSite({ outputRoot });
  const manifestPath = path.join(outputRoot, ".aurum-build.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const oldPage = "Obsolete generated page";
  await writeFile(path.join(outputRoot, "old.html"), oldPage);
  manifest.files["old.html"] = createHash("sha256")
    .update(oldPage)
    .digest("hex");
  await writeFile(manifestPath, JSON.stringify(manifest));
  await buildSite({ outputRoot });
  await assert.rejects(readFile(path.join(outputRoot, "old.html")), {
    code: "ENOENT",
  });
  await writeFile(path.join(outputRoot, "personal.txt"), "Keep this file");
  await assert.rejects(buildSite({ outputRoot }), /no files were deleted/u);
  assert.equal(
    await readFile(path.join(outputRoot, "personal.txt"), "utf8"),
    "Keep this file",
  );
});

test("build refuses directory links before writing into their target", async () => {
  const parent = await testOutput();
  const target = await testOutput();
  const linked = path.join(parent, "linked-output");
  await symlink(
    target,
    linked,
    process.platform === "win32" ? "junction" : "dir",
  );
  await assert.rejects(buildSite({ outputRoot: linked }), /links/u);
  await assert.rejects(readFile(path.join(target, "index.html")), {
    code: "ENOENT",
  });
});
