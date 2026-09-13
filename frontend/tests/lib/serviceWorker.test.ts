// @vitest-environment node
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";

const origin = "https://aurum.example";
const source = readFileSync(new URL("../../public/sw.js", import.meta.url), "utf8");

interface WorkerFetchEvent {
  request: Request;
  respondWith(response: Promise<Response>): void;
  waitUntil(work: Promise<unknown>): void;
}

function basicResponse(body: string, status = 200): Response {
  const response = new Response(body, { status });
  // Node constructs default responses; a same-origin browser fetch is basic.
  Object.defineProperty(response, "type", { value: "basic" });
  return response;
}

function createWorker() {
  const cacheEntries = new Map<string, Response>();
  const cache = {
    match: vi.fn(async (request: Request) => cacheEntries.get(request.url)?.clone()),
    put: vi.fn(async (request: Request, response: Response) => {
      cacheEntries.set(request.url, response.clone());
    }),
    keys: vi.fn(async () => [...cacheEntries.keys()].map((url) => new Request(url))),
    delete: vi.fn(async (request: Request) => cacheEntries.delete(request.url)),
  };
  const cacheStorage = {
    open: vi.fn(async () => cache),
    // Deliberately separate the all-cache lookup from the active cache.
    match: vi.fn(async (_request: Request): Promise<Response | undefined> => undefined),
  };
  const fetchNetwork = vi.fn<(request: Request, init?: RequestInit) => Promise<Response>>();
  const handlers = new Map<string, (event: WorkerFetchEvent) => void>();
  runInNewContext(source, {
    self: {
      location: { origin },
      addEventListener: (name: string, handler: (event: WorkerFetchEvent) => void) => {
        handlers.set(name, handler);
      },
    },
    caches: cacheStorage,
    fetch: fetchNetwork,
    URL,
    Response,
  });

  async function dispatch(path: string, method = "GET") {
    let intercepted: Promise<Response> | undefined;
    const pending: Promise<unknown>[] = [];
    const request = new Request(new URL(path, origin), { method });
    const handler = handlers.get("fetch");
    if (!handler) throw new Error("Service worker did not register a fetch handler");
    handler({
      request,
      respondWith: (response) => {
        intercepted = response;
      },
      waitUntil: (work) => {
        pending.push(work);
      },
    });
    const response = await intercepted;
    await Promise.all(pending);
    return response;
  }

  return { dispatch, fetchNetwork, cache, cacheStorage };
}

describe("service worker metadata freshness", () => {
  it.each(["/manifest.webmanifest", "/manifest.webmanifest?v=12-8", "/manifest.webmanifest?v=old"])(
    "refreshes cached metadata through HTTP revalidation for %s",
    async (path) => {
      const worker = createWorker();
      worker.fetchNetwork.mockResolvedValueOnce(basicResponse("old metadata"));
      expect(await (await worker.dispatch(path))?.text()).toBe("old metadata");
      worker.fetchNetwork.mockResolvedValueOnce(basicResponse("new metadata"));
      expect(await (await worker.dispatch(path))?.text()).toBe("new metadata");
      expect(worker.fetchNetwork).toHaveBeenCalledTimes(2);
      for (const [request, init] of worker.fetchNetwork.mock.calls) {
        expect(request.url).toBe(`${origin}${path}`);
        expect(init).toEqual({ cache: "no-cache" });
      }
      worker.fetchNetwork.mockRejectedValueOnce(new TypeError("Offline"));
      expect(await (await worker.dispatch(path))?.text()).toBe("new metadata");
      expect(worker.cacheStorage.match).not.toHaveBeenCalled();
    },
  );

  it.each([404, 500])("retains good metadata after HTTP %s", async (status) => {
    const worker = createWorker();
    worker.fetchNetwork.mockResolvedValueOnce(basicResponse("good metadata"));
    await worker.dispatch("/manifest.webmanifest");
    worker.fetchNetwork.mockResolvedValueOnce(basicResponse("server failure", status));
    const response = await worker.dispatch("/manifest.webmanifest");
    expect(response?.status).toBe(200);
    expect(await response?.text()).toBe("good metadata");
    expect(worker.cache.put).toHaveBeenCalledTimes(1);
    worker.fetchNetwork.mockRejectedValueOnce(new TypeError("Offline"));
    expect(await (await worker.dispatch("/manifest.webmanifest"))?.text()).toBe("good metadata");
  });

  it("does not cache opaque responses", async () => {
    const worker = createWorker();
    const opaque = new Response("opaque");
    Object.defineProperty(opaque, "type", { value: "opaque" });
    worker.fetchNetwork.mockResolvedValueOnce(opaque);
    await worker.dispatch("/manifest.webmanifest");
    expect(worker.cache.put).not.toHaveBeenCalled();
  });

  it("returns a network error when offline without current cached metadata", async () => {
    const worker = createWorker();
    worker.cacheStorage.match.mockResolvedValueOnce(basicResponse("obsolete cache"));
    worker.fetchNetwork.mockRejectedValueOnce(new TypeError("Offline"));
    const response = await worker.dispatch("/manifest.webmanifest");
    expect(response?.type).toBe("error");
    expect(response?.status).toBe(0);
    expect(worker.cacheStorage.match).not.toHaveBeenCalled();
    expect(worker.cache.put).not.toHaveBeenCalled();
  });

  it.each([404, 500])("preserves HTTP %s when there is no cached fallback", async (status) => {
    const worker = createWorker();
    worker.fetchNetwork.mockResolvedValueOnce(basicResponse("server failure", status));
    const response = await worker.dispatch("/manifest.webmanifest");
    expect(response?.status).toBe(status);
    expect(worker.cache.put).not.toHaveBeenCalled();
  });

  it("serves successful online metadata when storage is full", async () => {
    const worker = createWorker();
    worker.cache.put.mockRejectedValueOnce(new Error("Quota exceeded"));
    worker.fetchNetwork.mockResolvedValueOnce(basicResponse("fresh metadata"));
    const response = await worker.dispatch("/manifest.webmanifest");
    expect(response?.status).toBe(200);
    expect(await response?.text()).toBe("fresh metadata");
  });

  it.each([
    ["/api/v1/account.json", "GET"],
    ["/api/v1/export.png", "GET"],
    ["/login", "GET"],
    ["/index.html", "GET"],
    ["/manifest.webmanifest", "POST"],
    ["https://external.example/manifest.webmanifest", "GET"],
    ["/sw.js", "GET"],
  ])("does not intercept %s (%s)", async (path, method) => {
    const worker = createWorker();
    expect(await worker.dispatch(path, method)).toBeUndefined();
    expect(worker.fetchNetwork).not.toHaveBeenCalled();
    expect(worker.cacheStorage.open).not.toHaveBeenCalled();
    expect(worker.cacheStorage.match).not.toHaveBeenCalled();
  });

  it("preserves cache-first behavior for versioned icons", async () => {
    const worker = createWorker();
    worker.cacheStorage.match.mockResolvedValueOnce(basicResponse("cached icon"));
    expect(await (await worker.dispatch("/icons/icon-192.png?v=12-8"))?.text()).toBe("cached icon");
    expect(worker.fetchNetwork).not.toHaveBeenCalled();
  });
});
