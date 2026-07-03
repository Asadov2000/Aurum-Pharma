import {
  DESKTOP_USER_AGENT_TOKEN,
  applyRuntimeSurfaceAttribute,
  detectRuntimeSurface,
  type RuntimeDetectionTarget,
} from "@/lib/runtime";
import { afterEach, describe, expect, it } from "vitest";

describe("detectRuntimeSurface", () => {
  it("detects a regular browser by default", () => {
    expect(detectRuntimeSurface(createTarget())).toBe("browser");
  });

  it("detects an installed PWA from display-mode", () => {
    expect(detectRuntimeSurface(createTarget({ standaloneDisplay: true }))).toBe(
      "pwa",
    );
  });

  it("detects iOS standalone mode", () => {
    expect(detectRuntimeSurface(createTarget({ iosStandalone: true }))).toBe(
      "pwa",
    );
  });

  it("prefers the Windows desktop bridge over PWA display-mode", () => {
    expect(
      detectRuntimeSurface(
        createTarget({ hasWebViewBridge: true, standaloneDisplay: true }),
      ),
    ).toBe("windows-desktop");
  });

  it("detects the Windows desktop user-agent token", () => {
    expect(
      detectRuntimeSurface(
        createTarget({ userAgent: `Mozilla/5.0 ${DESKTOP_USER_AGENT_TOKEN}` }),
      ),
    ).toBe("windows-desktop");
  });
});

describe("applyRuntimeSurfaceAttribute", () => {
  afterEach(() => {
    delete document.documentElement.dataset.runtimeSurface;
  });

  it("writes the detected surface to the document root", () => {
    expect(applyRuntimeSurfaceAttribute("windows-desktop")).toBe(
      "windows-desktop",
    );
    expect(document.documentElement.dataset.runtimeSurface).toBe(
      "windows-desktop",
    );
  });
});

function createTarget(
  options: {
    hasWebViewBridge?: boolean;
    iosStandalone?: boolean;
    standaloneDisplay?: boolean;
    userAgent?: string;
  } = {},
): RuntimeDetectionTarget {
  const navigator = {
    standalone: options.iosStandalone,
    userAgent: options.userAgent ?? "Mozilla/5.0",
  } as Navigator & { readonly standalone?: boolean };

  return {
    chrome: options.hasWebViewBridge ? { webview: {} } : undefined,
    matchMedia: (query: string) =>
      createMediaQueryList(
        query === "(display-mode: standalone)" &&
          options.standaloneDisplay === true,
      ),
    navigator,
  };
}

function createMediaQueryList(matches: boolean): MediaQueryList {
  return {
    addEventListener: () => undefined,
    addListener: () => undefined,
    dispatchEvent: () => true,
    matches,
    media: "",
    onchange: null,
    removeEventListener: () => undefined,
    removeListener: () => undefined,
  } as MediaQueryList;
}
