import { readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const distDirectory = fileURLToPath(new URL("../dist/", import.meta.url));
const manifestPath = join(distDirectory, ".vite", "manifest.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
const entryKey = Object.keys(manifest).find((key) => manifest[key].isEntry);
if (!entryKey) throw new Error("Production entry was not found in the Vite manifest");

const routeBudgets = [
  {
    label: "Login /login",
    source: "src/features/auth/LoginPage.tsx",
    maxRawBytes: 525 * 1024,
    maxGzipBytes: 165 * 1024,
    maxRequests: 8,
  },
  {
    label: "Dashboard /",
    source: "src/features/dashboard/DashboardPage.tsx",
    maxRawBytes: 435 * 1024,
    maxGzipBytes: 140 * 1024,
    maxRequests: 6,
  },
  {
    label: "POS /pos",
    source: "src/features/pos/POSPage.tsx",
    // The public platform-activation route adds a small amount of shared
    // router metadata while its page remains a lazy chunk.
    maxRawBytes: 611 * 1024,
    maxGzipBytes: 195 * 1024,
    maxRequests: 19,
  },
];

for (const requiredSource of [
  "src/features/auth/LoginPage.tsx",
  "src/features/auth/MfaStepUpDialog.tsx",
]) {
  if (!manifest[requiredSource]?.isDynamicEntry) {
    throw new Error(`${requiredSource} must remain a lazy production chunk`);
  }
}

const entryResources = collectStaticResources([entryKey]);
assertResourcesExclude(entryResources, ["vendor-forms", "LoginPage", "MfaStepUpDialog"], "entry");

const routeResults = routeBudgets.map((budget) => {
  const resources = collectStaticResources([entryKey, budget.source]);
  const result = measureResources(resources);
  const failures = [];

  if (result.rawBytes > budget.maxRawBytes) {
    failures.push(`raw ${formatKiB(result.rawBytes)} > ${formatKiB(budget.maxRawBytes)}`);
  }
  if (result.gzipBytes > budget.maxGzipBytes) {
    failures.push(`gzip ${formatKiB(result.gzipBytes)} > ${formatKiB(budget.maxGzipBytes)}`);
  }
  if (resources.size > budget.maxRequests) {
    failures.push(`requests ${resources.size} > ${budget.maxRequests}`);
  }

  return { budget, resources, result, failures };
});

const dashboard = routeResults.find(({ budget }) => budget.source.includes("DashboardPage"));
const pos = routeResults.find(({ budget }) => budget.source.includes("POSPage"));
const login = routeResults.find(({ budget }) => budget.source.includes("LoginPage"));
assertResourcesExclude(
  dashboard.resources,
  ["vendor-forms", "LoginPage", "MfaStepUpDialog"],
  "Dashboard",
);
assertResourcesExclude(pos.resources, ["LoginPage", "MfaStepUpDialog"], "POS");
assertResourcesExclude(login.resources, ["MfaStepUpDialog"], "Login");

const report = routeResults
  .map(
    ({ budget, resources, result }) =>
      `${budget.label}: ${formatKiB(result.rawBytes)} raw / ${formatKiB(result.gzipBytes)} gzip (${resources.size} JS requests)`,
  )
  .join("\n");
process.stdout.write(`Route startup JavaScript:\n${report}\n`);

const failedRoutes = routeResults.filter(({ failures }) => failures.length > 0);
if (failedRoutes.length > 0) {
  throw new Error(
    `Route JavaScript budget exceeded:\n${failedRoutes
      .map(({ budget, failures }) => `- ${budget.label}: ${failures.join(", ")}`)
      .join("\n")}`,
  );
}

rmSync(manifestPath);

function collectStaticResources(initialKeys) {
  const resources = new Set();
  const visitedKeys = new Set();

  const visit = (key) => {
    if (visitedKeys.has(key)) return;
    visitedKeys.add(key);

    const chunk = manifest[key];
    if (!chunk) throw new Error(`Manifest entry is missing: ${key}`);
    if (chunk.file?.endsWith(".js")) resources.add(chunk.file);
    for (const importedKey of chunk.imports ?? []) visit(importedKey);
  };

  for (const key of initialKeys) visit(key);
  return resources;
}

function measureResources(resources) {
  let rawBytes = 0;
  let gzipBytes = 0;

  for (const resource of resources) {
    const contents = readFileSync(join(distDirectory, resource));
    rawBytes += contents.byteLength;
    gzipBytes += gzipSync(contents).byteLength;
  }

  return { rawBytes, gzipBytes };
}

function assertResourcesExclude(resources, forbiddenNames, label) {
  for (const forbiddenName of forbiddenNames) {
    if ([...resources].some((resource) => resource.includes(forbiddenName))) {
      throw new Error(`${forbiddenName} must not be loaded by ${label}`);
    }
  }
}

function formatKiB(bytes) {
  return `${(bytes / 1024).toFixed(2)} KiB`;
}
