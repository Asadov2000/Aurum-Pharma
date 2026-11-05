import { readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const distDirectory = fileURLToPath(new URL("../dist/", import.meta.url));
const manifestPath = join(distDirectory, ".vite", "manifest.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
const entryKey = Object.keys(manifest).find((key) => manifest[key].isEntry);
if (!entryKey) throw new Error("Production entry was not found in the Vite manifest");

const authenticatedShellSource = resolveAuthenticatedShell();

const routeBudgets = [
  {
    label: "Login /login",
    source: "src/features/auth/LoginPage.tsx",
    // Account-scoped sidebar storage is available before its lazy save bridge.
    // Bound that small shared-shell cost without adding startup requests.
    maxRawBytes: 530 * 1024,
    // ADR-0049 adds the shared durable logout/cookie coordination protocol.
    // Keep its bounded startup cost explicit rather than excluding auth chunks.
    maxGzipBytes: 171 * 1024,
    maxRequests: 8,
  },
  {
    label: "Dashboard /",
    source: "src/features/dashboard/DashboardPage.tsx",
    startupSources: [authenticatedShellSource],
    // Keep the full offline profile menu and owner-scoped sidebar storage eager.
    // The save engine remains lazy; the startup request cap stays fixed.
    maxRawBytes: 440 * 1024,
    maxGzipBytes: 145 * 1024,
    maxRequests: 6,
  },
  {
    label: "POS /pos",
    source: "src/features/pos/POSPage.tsx",
    startupSources: [authenticatedShellSource],
    // Public activation and the capability-gated billing workspace add small
    // shared router metadata while both page implementations remain lazy.
    maxRawBytes: 612 * 1024,
    maxGzipBytes: 195 * 1024,
    maxRequests: 19,
  },
];

for (const requiredSource of [
  authenticatedShellSource,
  "src/features/auth/LoginPage.tsx",
  "src/features/auth/MfaStepUpDialog.tsx",
  "src/features/dashboard/SupportProfile.tsx",
]) {
  if (!manifest[requiredSource]?.isDynamicEntry) {
    throw new Error(`${requiredSource} must remain a lazy production chunk`);
  }
}

const entryResources = collectStaticResources([entryKey]);
assertResourcesExclude(entryResources, ["vendor-forms", "LoginPage", "MfaStepUpDialog"], "entry");

const routeResults = routeBudgets.map((budget) => {
  // Protected routes render this lazy shell immediately: count it and all its
  // static dependencies rather than hiding startup bytes behind dynamic import.
  const resources = collectStaticResources([entryKey, budget.source, ...(budget.startupSources ?? [])]);
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
  ["vendor-forms", "LoginPage", "MfaStepUpDialog", "SupportProfile"],
  "Dashboard",
);
assertResourcesExclude(pos.resources, ["LoginPage", "MfaStepUpDialog"], "POS");
assertResourcesExclude(login.resources, ["MfaStepUpDialog", "AuthenticatedShell", "vendor-validation"], "Login");

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

function resolveAuthenticatedShell() {
  const source = "src/components/layout/AuthenticatedShell.tsx";
  const candidates = Object.entries(manifest).filter(([key, chunk]) =>
    chunk.isDynamicEntry && (
      key === source || chunk.src === source || chunk.name === "AuthenticatedShell" ||
      /^assets\/AuthenticatedShell-[^/]+\.js$/.test(chunk.file ?? "")
    ),
  );
  if (candidates.length !== 1) {
    throw new Error(`Expected one lazy AuthenticatedShell entry; found ${candidates.length}`);
  }
  const key = candidates[0][0];
  if (!(manifest[entryKey].dynamicImports ?? []).includes(key)) {
    throw new Error("AuthenticatedShell must be a dynamic import of the production entry");
  }
  return key;
}

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
