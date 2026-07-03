export const DESKTOP_USER_AGENT_TOKEN = "AurumPharmaDesktop";

export type RuntimeSurface = "browser" | "pwa" | "windows-desktop";

type RuntimeNavigator = Navigator & {
  readonly standalone?: boolean;
};

export interface RuntimeDetectionTarget {
  readonly navigator: RuntimeNavigator;
  readonly chrome?: {
    readonly webview?: unknown;
  };
  matchMedia(query: string): MediaQueryList;
}

export function detectRuntimeSurface(
  target: RuntimeDetectionTarget = window,
): RuntimeSurface {
  if (isWindowsDesktop(target)) {
    return "windows-desktop";
  }

  if (isStandaloneWebApp(target)) {
    return "pwa";
  }

  return "browser";
}

export function applyRuntimeSurfaceAttribute(
  surface: RuntimeSurface = detectRuntimeSurface(),
): RuntimeSurface {
  document.documentElement.dataset.runtimeSurface = surface;
  return surface;
}

function isWindowsDesktop(target: RuntimeDetectionTarget): boolean {
  return (
    target.chrome?.webview !== undefined ||
    target.navigator.userAgent.includes(DESKTOP_USER_AGENT_TOKEN)
  );
}

function isStandaloneWebApp(target: RuntimeDetectionTarget): boolean {
  return (
    target.navigator.standalone === true ||
    matchesDisplayMode(target, "standalone")
  );
}

function matchesDisplayMode(
  target: RuntimeDetectionTarget,
  mode: "standalone",
): boolean {
  try {
    return target.matchMedia(`(display-mode: ${mode})`).matches;
  } catch {
    return false;
  }
}
