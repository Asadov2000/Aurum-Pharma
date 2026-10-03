import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

import { verifyPatchedBraces } from "./braces-security.mjs";

const mitigatedId = "GHSA-vfj7-8cjw-p6xm";
const expiry = Date.parse("2026-11-03T00:00:00Z");
const supportedPath = /^\. > tailwindcss@3\.4\.\d+ > (chokidar@3\.6\.\d+ > |fast-glob@3\.3\.\d+ > micromatch@4\.0\.\d+ > |micromatch@4\.0\.\d+ > )braces@3\.0\.3$/;

export function evaluateAudit(report, verifyMitigation, now = Date.now()) {
  assert.ok(report && !report.error && report.advisories && report.metadata?.vulnerabilities,
    "Incomplete dependency audit; refusing to pass");
  const blockers = [];
  const advisories = Object.values(report.advisories);
  for (const severity of ["high", "critical"]) {
    assert.equal(report.metadata.vulnerabilities[severity], advisories.filter((item) => item.severity === severity).length,
      "Incomplete high/critical findings in audit report");
  }
  let verified = false;
  for (const advisory of advisories) {
    assert.ok(["low", "moderate", "high", "critical"].includes(advisory.severity), "Unknown audit severity");
    if (!["high", "critical"].includes(advisory.severity)) continue;
    if (advisory.github_advisory_id === mitigatedId && advisory.module_name === "braces") {
      assert.ok(now < expiry, "braces mitigation review expired; check upstream release");
      assert.ok(advisory.findings?.length > 0 && advisory.findings.every((finding) =>
        finding.version === "3.0.3" && finding.paths?.length > 0 && finding.paths.every((path) => supportedPath.test(path))),
      "braces appeared outside verified build dependencies");
      if (!verified) verifyMitigation();
      verified = true;
    } else {
      blockers.push(`${advisory.module_name}: ${advisory.github_advisory_id ?? advisory.id} (${advisory.severity})`);
    }
  }
  return { blockers, verified };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const windows = process.platform === "win32";
    const result = spawnSync(windows ? (process.env.ComSpec ?? "cmd.exe") : "pnpm",
      windows ? ["/d", "/s", "/c", "pnpm audit --json"] : ["audit", "--json"], {
      encoding: "utf8",
      maxBuffer: 10 * 1024 * 1024,
      timeout: 120_000,
    });
    assert.ok(!result.error && [0, 1].includes(result.status), "Dependency audit unavailable");
    const { blockers, verified } = evaluateAudit(JSON.parse(result.stdout), verifyPatchedBraces);
    if (verified) process.stdout.write(`${mitigatedId}: installed patch fingerprints and security regressions passed; review before 2026-11-03\n`);
    if (blockers.length) throw new Error(blockers.join("\n"));
    process.stdout.write("Dependency audit passed: no unmitigated high or critical vulnerabilities\n");
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
