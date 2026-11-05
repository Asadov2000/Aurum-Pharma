import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

import { E2E_POSTGRES_CONTAINER, E2E_POSTGRES_DB, E2E_REDIS_CONTAINER } from "./docker";

export interface SyntheticElectronicSale {
  sale_id: string;
  shift_id: string;
  receipt_number: string;
  total_amount: string;
}

/** Resolve only the backend belonging to the already verified disposable DB/Redis project. */
export function completeSyntheticElectronicSale(input: {
  tenantId: string;
  actorId: string;
  branchId: string;
  saleId: string;
  mode: "card" | "qr" | "split";
}): SyntheticElectronicSale {
  const immutableId = /^[a-f0-9]{12,64}$/;
  if (!immutableId.test(E2E_POSTGRES_CONTAINER) || !immutableId.test(E2E_REDIS_CONTAINER))
    throw new Error("Synthetic checkout requires immutable disposable E2E container IDs.");
  for (const value of [input.tenantId, input.actorId, input.branchId, input.saleId])
    if (!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value))
      throw new Error("Synthetic checkout scope must use UUIDs.");
  const inspect = (ids: string[]) =>
    execFileSync(
      "docker",
      [
        "inspect",
        "--format",
        '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}',
        ...ids,
      ],
      { encoding: "utf8", timeout: 15_000, stdio: ["ignore", "pipe", "pipe"] },
    )
      .trim()
      .split(/\r?\n/);
  const labels = inspect([E2E_POSTGRES_CONTAINER, E2E_REDIS_CONTAINER]);
  const [project, service] = (labels[0] ?? "").split("|");
  if (
    labels.length !== 2 ||
    !/^aurum-e2e-[a-z0-9-]+$/.test(project ?? "") ||
    service !== "postgres" ||
    labels[1] !== `${project}|redis`
  )
    throw new Error("Synthetic checkout requires the same disposable Postgres/Redis project.");
  const backend = execFileSync(
    "docker",
    [
      "ps",
      "--no-trunc",
      "--filter",
      `label=com.docker.compose.project=${project}`,
      "--filter",
      "label=com.docker.compose.service=backend",
      "--format",
      "{{.ID}}",
    ],
    { encoding: "utf8", timeout: 15_000, stdio: ["ignore", "pipe", "pipe"] },
  ).trim();
  if (!immutableId.test(backend) || inspect([backend])[0] !== `${project}|backend`)
    throw new Error("Synthetic checkout backend is not uniquely verified.");
  const script = readFileSync(
    new URL("./fixtures/electronic_physical_checkout.py", import.meta.url),
    "utf8",
  );
  let output: string;
  try {
    output = execFileSync(
      "docker",
      [
        "exec",
        "-i",
        backend,
        "python",
        "-",
        E2E_POSTGRES_DB,
        input.tenantId,
        input.actorId,
        input.branchId,
        input.saleId,
        input.mode,
      ],
      { input: script, encoding: "utf8", timeout: 45_000, stdio: ["pipe", "pipe", "pipe"] },
    );
  } catch {
    throw new Error("Synthetic checkout failed inside the verified disposable backend.");
  }
  return JSON.parse(output.trim()) as SyntheticElectronicSale;
}
