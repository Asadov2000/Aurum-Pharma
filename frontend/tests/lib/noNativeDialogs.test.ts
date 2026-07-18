import { promises as fs, readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SRC_DIR = path.resolve(process.cwd(), "src");
const SOURCE_EXTENSIONS = new Set([".ts", ".tsx"]);
const NATIVE_DIALOG_RE = /\b(?:window\.)?(?:confirm|alert)\s*\(/;

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(fullPath);
    return SOURCE_EXTENSIONS.has(path.extname(fullPath)) ? [fullPath] : [];
  });
}

describe("frontend native dialogs", () => {
  it("uses app dialogs instead of browser confirm/alert", async () => {
    const checked = await Promise.all(
      sourceFiles(SRC_DIR).map(async (file) => ({
        file,
        source: await fs.readFile(file, "utf8"),
      })),
    );
    const offenders = checked
      .filter(({ source }) => NATIVE_DIALOG_RE.test(source))
      .map(({ file }) => file);

    expect(offenders.map((file) => path.relative(process.cwd(), file))).toEqual([]);
  });
});
