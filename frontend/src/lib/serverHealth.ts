import { apiBaseUrl } from "@/lib/api";

const DEFAULT_HEALTH_PATH = "/healthz";
const DEFAULT_HEALTH_TIMEOUT_MS = 8_000;

interface HealthPayload {
  readonly status?: string;
}

interface CheckServerHealthOptions {
  readonly fetcher?: typeof fetch;
  readonly healthUrl?: string;
  readonly signal?: AbortSignal;
  readonly timeoutMs?: number;
}

export function resolveServerHealthUrl(apiUrl = apiBaseUrl, origin = getWindowOrigin()): string {
  const resolved = new URL(apiUrl, origin);
  const isRelative = isRelativeUrl(apiUrl);

  resolved.pathname = DEFAULT_HEALTH_PATH;
  resolved.search = "";
  resolved.hash = "";

  if (isRelative) {
    return resolved.pathname;
  }

  return resolved.toString();
}

export async function checkServerHealth(options: CheckServerHealthOptions = {}): Promise<boolean> {
  const fetcher = options.fetcher ?? getFetch();
  if (!fetcher) {
    return false;
  }

  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    options.timeoutMs ?? DEFAULT_HEALTH_TIMEOUT_MS,
  );
  const abortFromCaller = () => controller.abort();

  if (options.signal?.aborted) {
    controller.abort();
  } else {
    options.signal?.addEventListener("abort", abortFromCaller, { once: true });
  }

  try {
    const response = await fetcher(options.healthUrl ?? resolveServerHealthUrl(), {
      cache: "no-store",
      headers: {
        Accept: "application/json",
      },
      method: "GET",
      signal: controller.signal,
    });

    if (!response.ok) {
      return false;
    }

    const payload = (await response.json()) as HealthPayload;
    return payload.status === "ok";
  } catch {
    return false;
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abortFromCaller);
  }
}

function isRelativeUrl(value: string): boolean {
  try {
    new URL(value);
    return false;
  } catch {
    return true;
  }
}

function getWindowOrigin(): string {
  if (typeof window !== "undefined") {
    return window.location.origin;
  }

  return "http://localhost";
}

function getFetch(): typeof fetch | null {
  return typeof fetch === "undefined" ? null : fetch.bind(globalThis);
}
