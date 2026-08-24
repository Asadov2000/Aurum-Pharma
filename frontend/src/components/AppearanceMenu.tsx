import { createPortal } from "react-dom";
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/ui";
import { getDensityPreference, setDensityPreference, type DensityPreference } from "@/lib/density";
import { applyUserPreferences } from "@/features/settings/appearance";
import { usePreferenceAutosave } from "@/features/settings/usePreferenceAutosave";
import { getThemePreference, setThemePreference, type ThemePreference } from "@/lib/theme";

const POPOVER_WIDTH = 360;
const VIEWPORT_GUTTER = 8;

const THEME_OPTIONS = [
  { value: "light", label: "Светлая" },
  { value: "dark", label: "Тёмная" },
  { value: "system", label: "Система" },
] as const;

const DENSITY_OPTIONS = [
  { value: "auto", label: "Авто" },
  { value: "compact", label: "Плотно" },
  { value: "comfortable", label: "Обычно" },
  { value: "touch", label: "Сенсор" },
] as const;

export function AppearanceMenu(): JSX.Element {
  const titleId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<ThemePreference>(() => getThemePreference());
  const [density, setDensity] = useState<DensityPreference>(() => getDensityPreference());
  const [position, setPosition] = useState({ left: VIEWPORT_GUTTER, top: VIEWPORT_GUTTER });
  const autosave = usePreferenceAutosave("header-appearance");
  const preferences = autosave.preferences;

  useEffect(() => {
    if (!preferences.data || autosave.hasPending) return;
    applyUserPreferences(preferences.data);
    setTheme(preferences.data.theme);
    setDensity(preferences.data.density);
  }, [autosave.hasPending, preferences.data]);

  const syncPreference = (patch: { theme: ThemePreference } | { density: DensityPreference }) => {
    if (!preferences.data) return;
    autosave.enqueue(patch);
  };

  const close = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) requestAnimationFrame(() => triggerRef.current?.focus());
  };

  const show = () => {
    const trigger = triggerRef.current;
    if (!trigger) return;

    const rect = trigger.getBoundingClientRect();
    const width = Math.min(POPOVER_WIDTH, window.innerWidth - VIEWPORT_GUTTER * 2);
    setPosition({
      left: Math.max(
        VIEWPORT_GUTTER,
        Math.min(rect.right - width, window.innerWidth - width - VIEWPORT_GUTTER),
      ),
      top: rect.bottom + VIEWPORT_GUTTER,
    });
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return undefined;

    const focusFrame = requestAnimationFrame(() => {
      popoverRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    });
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (popoverRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close(true);
    };
    const onViewportChange = () => close();

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, true);
    return () => {
      cancelAnimationFrame(focusFrame);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", onViewportChange);
      window.removeEventListener("scroll", onViewportChange, true);
    };
  }, [open]);

  return (
    <>
      <Button
        ref={triggerRef}
        variant="secondary"
        size="sm"
        className="w-[var(--control-height-sm)] px-0"
        aria-label="Вид интерфейса"
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Вид интерфейса"
        onClick={() => (open ? close() : show())}
      >
        <AppearanceIcon />
      </Button>

      {open &&
        createPortal(
          <div
            ref={popoverRef}
            role="dialog"
            aria-modal="false"
            aria-labelledby={titleId}
            className="fixed z-popover w-[min(20rem,calc(100vw-1rem))] rounded-lg border border-border bg-surface-raised p-4 shadow-lg"
            style={position}
          >
            <h2 id={titleId} className="text-sm font-semibold text-foreground">
              Вид интерфейса
            </h2>
            <div className="mt-4 space-y-4">
              <div>
                <p className="mb-1.5 text-xs font-medium text-foreground-muted">Тема</p>
                <AppearanceOptions
                  value={theme}
                  options={THEME_OPTIONS}
                  label="Тема оформления"
                  columns="grid-cols-3"
                  onChange={(value) => {
                    setThemePreference(value);
                    setTheme(value);
                    syncPreference({ theme: value });
                  }}
                />
              </div>
              <div>
                <p className="mb-1.5 text-xs font-medium text-foreground-muted">Размер элементов</p>
                <AppearanceOptions
                  value={density}
                  options={DENSITY_OPTIONS}
                  label="Плотность интерфейса"
                  columns="grid-cols-2"
                  onChange={(value) => {
                    setDensityPreference(value);
                    setDensity(value);
                    syncPreference({ density: value });
                  }}
                />
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}

function AppearanceOptions<T extends string>({
  value,
  options,
  label,
  columns,
  onChange,
}: {
  value: T;
  options: readonly { value: T; label: string }[];
  label: string;
  columns: "grid-cols-2" | "grid-cols-3";
  onChange: (value: T) => void;
}): JSX.Element {
  return (
    <div
      role="group"
      aria-label={label}
      className={`grid ${columns} overflow-hidden rounded-md border border-border bg-surface`}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          className="min-h-9 border-r border-border px-2 text-xs font-medium text-foreground transition-colors last:border-r-0 hover:bg-surface-subtle aria-pressed:bg-primary aria-pressed:text-primary-foreground"
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function AppearanceIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    >
      <path d="M4 7h10" />
      <path d="M18 7h2" />
      <path d="M4 17h2" />
      <path d="M10 17h10" />
      <circle cx="16" cy="7" r="2" />
      <circle cx="8" cy="17" r="2" />
    </svg>
  );
}
