import assert from "node:assert/strict";
import test from "node:test";
import { evaluateAudit } from "./audit-dependencies.mjs";
import { verifyPatchedBraces } from "./braces-security.mjs";

const finding = {
  module_name: "braces",
  github_advisory_id: "GHSA-vfj7-8cjw-p6xm",
  severity: "high",
  findings: [{ version: "3.0.3", paths: [". > tailwindcss@3.4.19 > chokidar@3.6.0 > braces@3.0.3"] }],
};
const report = (advisories) => ({ advisories, metadata: { vulnerabilities: {
  high: Object.values(advisories).filter((item) => item.severity === "high").length,
  critical: Object.values(advisories).filter((item) => item.severity === "critical").length,
} } });
const now = Date.parse("2026-10-03");

test("installed braces backport blocks deep strings and direct ASTs while retaining ordinary patterns", verifyPatchedBraces);
test("only the verified advisory is mitigated; new high and critical findings remain blockers", () => {
  let checks = 0;
  const result = evaluateAudit(report({ a: finding, b: { ...finding, github_advisory_id: "GHSA-new", severity: "critical" } }), () => checks++, now);
  assert.equal(checks, 1);
  assert.equal(result.blockers.length, 1);
});
test("missing protection and unexpected dependency paths cannot pass", () => {
  assert.throws(() => evaluateAudit(report({ a: finding }), () => { throw new Error("Missing patch"); }, now), /Missing patch/);
  const runtime = { ...finding, findings: [{ version: "3.0.3", paths: [". > braces@3.0.3"] }] };
  assert.throws(() => evaluateAudit(report({ a: runtime }), () => {}, now), /outside verified/);
});
test("unavailable audit and expired mitigation fail closed", () => {
  assert.throws(() => evaluateAudit({ error: "Network failure" }, () => {}, now), /Incomplete/);
  assert.throws(() => evaluateAudit({ advisories: {}, metadata: { vulnerabilities: { high: 1, critical: 0 } } }, () => {}, now), /Incomplete/);
  assert.throws(() => evaluateAudit(report({ a: finding }), () => {}, Date.parse("2026-11-03")), /expired/);
});
