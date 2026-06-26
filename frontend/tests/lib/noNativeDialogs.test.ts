import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SRC_DIR = path.resolve(process.cwd(), "src");
const SOURCE_EXTENSIONS = new Set([".ts", ".tsx"]);
const NATIVE_DIALOG_RE = /\b(?:window\.)?(?:confirm|alert)\s*\(/;

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const fullPath = path.join(dir, entry);
    const stat = statSync(fullPath);
    if (stat.isDirectory()) return sourceFiles(fullPath);
    return SOURCE_EXTENSIONS.has(path.extname(fullPath)) ? [fullPath] : [];
  });
}

describe("frontend native dialogs", () => {
  it("uses app dialogs instead of browser confirm/alert", () => {
    const offenders = sourceFiles(SRC_DIR).filter((file) =>
      NATIVE_DIALOG_RE.test(readFileSync(file, "utf8")),
    );

    expect(offenders.map((file) => path.relative(process.cwd(), file))).toEqual([]);
  });
});
