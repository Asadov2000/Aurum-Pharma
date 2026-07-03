import { PWA_UPDATE_READY_EVENT, registerPwaServiceWorker } from "@/lib/pwa";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("registerPwaServiceWorker", () => {
  beforeEach(() => {
    vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("skips registration in the Vite test environment", () => {
    const originalDescriptor = Object.getOwnPropertyDescriptor(
      window.navigator,
      "serviceWorker",
    );
    const serviceWorker = { register: vi.fn() };
    Object.defineProperty(window.navigator, "serviceWorker", {
      configurable: true,
      value: serviceWorker,
    });
    const addEventListener = vi.spyOn(window, "addEventListener");

    try {
      registerPwaServiceWorker();

      expect(addEventListener).not.toHaveBeenCalledWith(
        "load",
        expect.any(Function),
      );
      expect(serviceWorker.register).not.toHaveBeenCalled();
    } finally {
      if (originalDescriptor) {
        Object.defineProperty(window.navigator, "serviceWorker", originalDescriptor);
      } else {
        Reflect.deleteProperty(window.navigator, "serviceWorker");
      }
    }
  });

  it("registers /sw.js after the page load outside dev mode", async () => {
    const serviceWorker = createServiceWorkerContainer();

    registerPwaServiceWorker({
      isDev: false,
      serviceWorker,
    });

    window.dispatchEvent(new Event("load"));
    await Promise.resolve();

    expect(serviceWorker.register).toHaveBeenCalledWith("/sw.js");
  });

  it("notifies the app when an update is installed over an existing controller", async () => {
    const serviceWorker = createServiceWorkerContainer({
      controller: createServiceWorker(),
    });
    const updateReady = vi.fn();
    window.addEventListener(PWA_UPDATE_READY_EVENT, updateReady);

    try {
      registerPwaServiceWorker({
        isDev: false,
        serviceWorker,
      });

      window.dispatchEvent(new Event("load"));
      await Promise.resolve();

      const installing = createServiceWorker("installing");
      serviceWorker.registration.installing = installing;
      serviceWorker.registration.dispatchEvent(new Event("updatefound"));
      installing.state = "installed";
      installing.dispatchEvent(new Event("statechange"));

      expect(updateReady).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(PWA_UPDATE_READY_EVENT, updateReady);
    }
  });

  it("does not notify on the first service worker installation", async () => {
    const serviceWorker = createServiceWorkerContainer();
    const updateReady = vi.fn();
    window.addEventListener(PWA_UPDATE_READY_EVENT, updateReady);

    try {
      registerPwaServiceWorker({
        isDev: false,
        serviceWorker,
      });

      window.dispatchEvent(new Event("load"));
      await Promise.resolve();

      const installing = createServiceWorker("installing");
      serviceWorker.registration.installing = installing;
      serviceWorker.registration.dispatchEvent(new Event("updatefound"));
      installing.state = "installed";
      installing.dispatchEvent(new Event("statechange"));

      expect(updateReady).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener(PWA_UPDATE_READY_EVENT, updateReady);
    }
  });
});

class MockServiceWorkerRegistration extends EventTarget {
  installing: MockServiceWorker | null = null;
  waiting: ServiceWorker | null = null;
}

class MockServiceWorker extends EventTarget {
  constructor(public state: ServiceWorkerState = "activated") {
    super();
  }
}

interface MockServiceWorkerContainer extends ServiceWorkerContainer {
  readonly register: ReturnType<typeof vi.fn>;
  readonly registration: MockServiceWorkerRegistration;
}

function createServiceWorkerContainer(options?: {
  readonly controller?: ServiceWorker;
}): MockServiceWorkerContainer {
  const registration = new MockServiceWorkerRegistration();
  const target = new EventTarget();

  return Object.assign(target, {
    controller: options?.controller ?? null,
    getRegistration: vi.fn(),
    getRegistrations: vi.fn(),
    ready: Promise.resolve(registration as unknown as ServiceWorkerRegistration),
    register: vi.fn(async () => registration as unknown as ServiceWorkerRegistration),
    registration,
    startMessages: vi.fn(),
  }) as MockServiceWorkerContainer;
}

function createServiceWorker(state: ServiceWorkerState = "activated"): ServiceWorker {
  return new MockServiceWorker(state) as unknown as ServiceWorker;
}
