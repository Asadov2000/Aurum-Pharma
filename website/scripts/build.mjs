import {
  mkdir,
  readdir,
  readFile,
  writeFile,
  lstat,
  unlink,
} from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const websiteRoot = fileURLToPath(new URL("../", import.meta.url));
export const repositoryRoot = path.resolve(websiteRoot, "..");
const sourceRoot = path.join(websiteRoot, "src");

export function resolveAppUrl(value, production = false) {
  const input =
    value?.trim() || (production ? "" : "http://localhost:5173/login");
  if (!input || /[\u0000-\u0020\\]/u.test(input)) {
    throw new Error(
      "APP_URL must be an explicit application URL without whitespace.",
    );
  }
  let url;
  try {
    url = new URL(input);
  } catch {
    throw new Error("APP_URL must be an absolute application URL.");
  }
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !["/", "/login"].includes(url.pathname)
  ) {
    throw new Error(
      "APP_URL must contain only the application origin and optional /login path.",
    );
  }
  if (
    production &&
    (url.protocol !== "https:" ||
      /^(localhost|.*\.localhost|127\..*|\[::1\])$/iu.test(
        url.hostname.replace(/\.$/u, ""),
      ))
  ) {
    throw new Error(
      "Production APP_URL requires HTTPS and a non-loopback application host.",
    );
  }
  url.pathname = "/login";
  return url.href;
}

export function escapeAttribute(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("'", "&#39;");
}

async function sourceFiles(directory, prefix = "") {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isSymbolicLink())
      throw new Error("Website source cannot contain symbolic links.");
    if (entry.isDirectory()) {
      files.push(
        ...(await sourceFiles(path.join(directory, entry.name), relative)),
      );
    } else if (entry.isFile()) {
      if (!/\.(html|css|js|svg|png|webp|ico|txt)$/u.test(entry.name)) {
        throw new Error(`Unsupported website source file: ${relative}`);
      }
      files.push(relative);
    }
  }
  return files.sort();
}

const manifestName = ".aurum-build.json";
const checksum = (value) => createHash("sha256").update(value).digest("hex");

function isWithin(directory, target) {
  const relative = path.relative(directory, target);
  return (
    relative === "" ||
    (!relative.startsWith(`..${path.sep}`) &&
      relative !== ".." &&
      !path.isAbsolute(relative))
  );
}

async function checkedDirectory(directory) {
  // lstat every component beneath the workspace: Windows junctions are links too.
  let current = repositoryRoot;
  for (const part of path
    .relative(repositoryRoot, directory)
    .split(path.sep)
    .filter(Boolean)) {
    current = path.join(current, part);
    try {
      const info = await lstat(current);
      if (info.isSymbolicLink() || !info.isDirectory())
        throw new Error("Website output cannot follow links or replace files.");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      await mkdir(current);
    }
  }
}

async function existingOutputFiles(directory, prefix = "") {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isSymbolicLink())
      throw new Error("Website output cannot contain links.");
    if (entry.isDirectory())
      files.push(
        ...(await existingOutputFiles(
          path.join(directory, entry.name),
          relative,
        )),
      );
    else if (entry.isFile()) files.push(relative);
    else throw new Error("Website output contains an unsupported entry.");
  }
  return files;
}

export async function buildSite({
  appUrl,
  production = false,
  outputRoot = path.join(websiteRoot, "dist"),
} = {}) {
  const resolvedAppUrl = resolveAppUrl(appUrl, production);
  const absoluteOutput = path.resolve(outputRoot);
  const relativeOutput = path.relative(repositoryRoot, absoluteOutput);
  if (
    !relativeOutput ||
    relativeOutput.startsWith("..") ||
    path.isAbsolute(relativeOutput)
  ) {
    throw new Error("Website output must stay within this repository.");
  }
  if (isWithin(sourceRoot, absoluteOutput)) {
    throw new Error("Website output cannot overwrite its source.");
  }
  if (
    path.relative(path.join(websiteRoot, "dist"), absoluteOutput) !== "" &&
    !isWithin(
      path.join(repositoryRoot, ".codex", "website-tests"),
      absoluteOutput,
    )
  ) {
    throw new Error(
      "Website output is restricted to website/dist or .codex/website-tests.",
    );
  }
  const files = await sourceFiles(sourceRoot);
  if (
    !files.includes("index.html") ||
    !files.includes("downloads/index.html")
  ) {
    throw new Error("Website home and downloads pages are required.");
  }
  const rendered = new Map();
  for (const relative of files) {
    const input = await readFile(path.join(sourceRoot, relative));
    let output = input;
    if (relative.endsWith(".html")) {
      const html = input
        .toString("utf8")
        .replaceAll("{{APP_URL}}", escapeAttribute(resolvedAppUrl));
      if (/\{\{[^}]+\}\}/u.test(html))
        throw new Error(`Unresolved template in ${relative}`);
      output = html;
    }
    rendered.set(relative, output);
  }
  await checkedDirectory(absoluteOutput);
  const existing = await existingOutputFiles(absoluteOutput);
  let previous = {};
  if (existing.includes(manifestName)) {
    const manifest = JSON.parse(
      await readFile(path.join(absoluteOutput, manifestName), "utf8"),
    );
    if (
      manifest.version !== 1 ||
      !manifest.files ||
      typeof manifest.files !== "object" ||
      Array.isArray(manifest.files)
    )
      throw new Error("Invalid website build manifest.");
    previous = manifest.files;
    for (const [relative, hash] of Object.entries(previous)) {
      if (
        !relative ||
        relative.includes("\\") ||
        relative.startsWith("/") ||
        relative.split("/").some((part) => ["", ".", ".."].includes(part)) ||
        relative === manifestName ||
        !/^[a-f0-9]{64}$/u.test(hash)
      ) {
        throw new Error("Invalid website build manifest entry.");
      }
    }
  }
  const obsolete = [];
  for (const relative of existing) {
    if (relative === manifestName || rendered.has(relative)) continue;
    if (
      !Object.hasOwn(previous, relative) ||
      checksum(await readFile(path.join(absoluteOutput, relative))) !==
        previous[relative]
    ) {
      throw new Error(
        "Unrecognized or modified file in website output; no files were deleted.",
      );
    }
    obsolete.push(relative);
  }
  // Remove only hash-verified obsolete artifacts, one file at a time. No recursive deletion.
  for (const relative of obsolete)
    await unlink(path.join(absoluteOutput, relative));
  for (const [relative, output] of rendered) {
    const destination = path.join(absoluteOutput, relative);
    await checkedDirectory(path.dirname(destination));
    await writeFile(destination, output);
  }
  await writeFile(
    path.join(absoluteOutput, manifestName),
    `${JSON.stringify({ version: 1, files: Object.fromEntries([...rendered].map(([relative, value]) => [relative, checksum(value)])) }, null, 2)}\n`,
  );
  return { files, appUrl: resolvedAppUrl, outputRoot: absoluteOutput };
}

if (
  process.argv[1] &&
  pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url
) {
  try {
    if (process.argv.slice(2).some((argument) => argument !== "--production")) {
      throw new Error("Usage: node website/scripts/build.mjs [--production]");
    }
    const result = await buildSite({
      appUrl: process.env.APP_URL,
      production: process.argv.includes("--production"),
      ...(process.env.WEBSITE_OUTPUT_DIR
        ? {
            outputRoot: path.resolve(
              repositoryRoot,
              process.env.WEBSITE_OUTPUT_DIR,
            ),
          }
        : {}),
    });
    process.stdout.write(`Website built: ${result.files.length} files.\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
