import { useState } from "react";

import { cn } from "@/lib/utils";
import { getThemePreference, setThemePreference, type ThemePreference } from "@/lib/theme";

const OPTIONS: { value: ThemePreference; label: string; title: string }[] = [
  { value: "light", label: "☀", title: "Светлая" },
  { value: "dark", label: "☾", title: "Тёмная" },
  { value: "system", label: "◐", title: "Системная" },
];

/** Three-way segmented control for the theme. Persists instantly. */
export function ThemeToggle(): JSX.Element {
  const [pref, setPref] = useState<ThemePreference>(() => getThemePreference());

  const choose = (value: ThemePreference) => {
    setThemePreference(value);
    setPref(value);
  };

  return (
    <div
      role="group"
      aria-label="Тема оформления"
      className="inline-flex items-center rounded-md border border-border bg-background p-0.5"
    >
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          title={o.title}
          aria-label={o.title}
          aria-pressed={pref === o.value}
          onClick={() => choose(o.value)}
          className={cn(
            "h-7 w-8 rounded-md text-sm transition-colors duration-fast",
            pref === o.value
              ? "bg-surface text-foreground shadow-sm"
              : "text-foreground-muted hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
