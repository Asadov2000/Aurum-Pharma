import { Badge } from "@/components/ui";
import { detectRuntimeSurface, type RuntimeSurface } from "@/lib/runtime";
import { cn } from "@/lib/utils";

type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info";

const SURFACE_META: Record<RuntimeSurface, { label: string; title: string; tone: BadgeTone }> = {
  browser: {
    label: "Веб",
    title: "Открыто в браузере",
    tone: "neutral",
  },
  pwa: {
    label: "PWA",
    title: "Установлено как приложение",
    tone: "info",
  },
  "windows-desktop": {
    label: "Windows",
    title: "Открыто в Windows-приложении",
    tone: "success",
  },
};

export function RuntimeSurfaceBadge({
  surface = detectRuntimeSurface(),
  className,
}: {
  surface?: RuntimeSurface;
  className?: string;
}): JSX.Element {
  const meta = SURFACE_META[surface];

  return (
    <Badge
      aria-label={`Режим запуска: ${meta.title}`}
      className={cn("hidden max-w-[5.5rem] shrink-0 truncate md:inline-flex", className)}
      data-testid="runtime-surface-badge"
      title={meta.title}
      tone={meta.tone}
    >
      {meta.label}
    </Badge>
  );
}
