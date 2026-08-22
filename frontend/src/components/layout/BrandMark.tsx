import { cn } from "@/lib/utils";

export function BrandMark({
  className,
  showName = false,
  tone = "default",
}: {
  className?: string;
  showName?: boolean;
  tone?: "default" | "inverse";
}): JSX.Element {
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-3", className)}>
      <span
        aria-hidden="true"
        className={cn(
          "relative grid h-9 w-9 shrink-0 place-items-center rounded-full shadow-sm",
          tone === "inverse"
            ? "bg-shell-sidebar-foreground text-shell-sidebar"
            : "bg-primary text-primary-foreground",
        )}
      >
        <span className="absolute h-4 w-1.5 rounded-sm bg-current" />
        <span className="absolute h-1.5 w-4 rounded-sm bg-current" />
      </span>
      {showName && (
        <span
          className={cn(
            "truncate font-display text-lg font-semibold",
            tone === "inverse" ? "text-shell-sidebar-foreground" : "text-foreground",
          )}
        >
          Aurum Pharma
        </span>
      )}
    </span>
  );
}
