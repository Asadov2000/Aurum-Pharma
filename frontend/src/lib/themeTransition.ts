import {
  resolveTheme,
  setThemePreference,
  type ResolvedTheme,
  type ThemePreference,
} from "./theme";

interface ThemeViewTransition {
  ready: Promise<void>;
  finished: Promise<void>;
  skipTransition(): void;
}

type ThemeTransitionDocument = Document & {
  startViewTransition?: (update: () => void) => ThemeViewTransition;
};

type Point = { x: number; y: number };

const DURATION_MS = 10000;
const AUTO_START_MS = 8000;
const TRANSITION_CLASS = "theme-slant-transition";
const LEFT_CLASS = "theme-slant-from-left";
const RIGHT_CLASS = "theme-slant-from-right";

let recentPointer: { point: Point; time: number } | null = null;
let activeTransition: ThemeViewTransition | null = null;
let activeCleanup: (() => void) | null = null;

if (typeof window !== "undefined") {
  const rememberPointer = (event: PointerEvent) => {
    recentPointer = { point: { x: event.clientX, y: event.clientY }, time: performance.now() };
  };
  window.addEventListener("pointermove", rememberPointer, { passive: true });
  window.addEventListener("pointerdown", rememberPointer, { passive: true });
}

function originPoint(): Point {
  if (recentPointer && performance.now() - recentPointer.time < 3000) return recentPointer.point;
  const focused = document.activeElement?.getBoundingClientRect();
  if (focused && focused.width > 0 && focused.height > 0) {
    return { x: focused.left + focused.width / 2, y: focused.top + focused.height / 2 };
  }
  return { x: window.innerWidth / 2, y: window.innerHeight / 2 };
}

const clamp = (value: number, maximum: number): number => Math.max(0, Math.min(maximum, value));

/** Animate only explicit user choices; startup and server preference sync stay immediate. */
export function setThemePreferenceWithTransition(pref: ThemePreference): ResolvedTheme {
  const nextTheme = resolveTheme(pref);
  const root = document.documentElement;
  const currentTheme = root.getAttribute("data-theme");
  const transitionDocument = document as ThemeTransitionDocument;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const hasFinePointer = window.matchMedia("(pointer: fine)").matches;
  const hasRecentPointer = recentPointer !== null && performance.now() - recentPointer.time < 3000;

  if (
    currentTheme === nextTheme ||
    reduceMotion ||
    !hasFinePointer ||
    !hasRecentPointer ||
    root.getAttribute("data-reduce-motion") === "true" ||
    !transitionDocument.startViewTransition
  ) {
    return setThemePreference(pref);
  }

  activeTransition?.skipTransition();
  activeCleanup?.();

  const width = window.innerWidth;
  const height = window.innerHeight;
  const origin = originPoint();
  const fromRight = origin.x >= width / 2;
  let top = clamp(origin.x, width);
  let bottom = top;
  let pointer = origin;
  let frame = 0;
  let transition: ThemeViewTransition;
  let cleaned = false;

  const setEdge = () => {
    root.style.setProperty("--theme-slant-top", `${top}px`);
    root.style.setProperty("--theme-slant-bottom", `${bottom}px`);
  };
  const onPointerMove = (event: PointerEvent) => {
    pointer = { x: event.clientX, y: event.clientY };
  };
  const onResize = () => transition.skipTransition();
  const cleanup = () => {
    if (cleaned) return;
    cleaned = true;
    cancelAnimationFrame(frame);
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("resize", onResize);
    root.classList.remove(TRANSITION_CLASS, LEFT_CLASS, RIGHT_CLASS);
    root.style.removeProperty("--theme-slant-top");
    root.style.removeProperty("--theme-slant-bottom");
    if (activeTransition === transition) {
      activeTransition = null;
      activeCleanup = null;
    }
  };

  root.classList.add(TRANSITION_CLASS, fromRight ? RIGHT_CLASS : LEFT_CLASS);
  setEdge();

  try {
    transition = transitionDocument.startViewTransition(() => {
      setThemePreference(pref);
    });
  } catch {
    cleanup();
    return setThemePreference(pref);
  }

  activeTransition = transition;
  activeCleanup = cleanup;
  transition.ready.then(
    () => {
      if (activeTransition !== transition) return;
      const started = performance.now();
      window.addEventListener("pointermove", onPointerMove, { passive: true });
      window.addEventListener("resize", onResize);

      const draw = (now: number) => {
        if (activeTransition !== transition) return;
        const elapsed = now - started;
        const automatic = clamp((elapsed - AUTO_START_MS) / (DURATION_MS - AUTO_START_MS), 1);
        const eased = automatic * automatic * (3 - 2 * automatic);
        const lean = (height / 2 - pointer.y) * 0.42;
        const pointerTop = clamp(pointer.x + lean, width);
        const pointerBottom = clamp(pointer.x - lean, width);
        if (fromRight) {
          const automaticEdge = width * (1 - eased);
          top = Math.min(top, pointerTop, automaticEdge);
          bottom = Math.min(bottom, pointerBottom, automaticEdge);
        } else {
          const automaticEdge = width * eased;
          top = Math.max(top, pointerTop, automaticEdge);
          bottom = Math.max(bottom, pointerBottom, automaticEdge);
        }
        setEdge();
        if (top <= 0 && bottom <= 0 && fromRight) {
          transition.skipTransition();
        } else if (top >= width && bottom >= width && !fromRight) {
          transition.skipTransition();
        } else if (automatic < 1) {
          frame = requestAnimationFrame(draw);
        }
      };
      frame = requestAnimationFrame(draw);
    },
    () => {
      if (activeTransition === transition) cleanup();
    },
  );
  transition.finished.then(cleanup, cleanup);
  return nextTheme;
}
