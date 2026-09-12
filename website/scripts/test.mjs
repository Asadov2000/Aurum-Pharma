import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const files = ["build.test.mjs", "server.test.mjs"].map((name) =>
  fileURLToPath(new URL(`../tests/${name}`, import.meta.url)),
);
const result = spawnSync(
  process.execPath,
  ["--test", "--test-reporter=spec", ...files],
  { stdio: "inherit" },
);
if (result.error) process.stderr.write(`${result.error.message}\n`);
process.exitCode = result.status ?? 1;
