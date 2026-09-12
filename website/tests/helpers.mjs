import { mkdir, mkdtemp } from "node:fs/promises";
import path from "node:path";
import { repositoryRoot } from "../scripts/build.mjs";

export async function testOutput() {
  const parent = path.join(repositoryRoot, ".codex", "website-tests");
  await mkdir(parent, { recursive: true });
  return mkdtemp(path.join(parent, "run-"));
}
