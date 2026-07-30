import { cn } from "@/lib/utils";

export function BrandMark({
  className,
  showName = false,
}: {
  className?: string;
  showName?: boolean;
}): JSX.Element {
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-3", className)}>
      <span
        aria-hidden="true"
        className="relative grid h-9 w-9 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground shadow-sm"
      >
        <span className="absolute h-4 w-1.5 rounded-sm bg-current" />
        <span className="absolute h-1.5 w-4 rounded-sm bg-current" />
      </span>
      {showName && (
        <span className="truncate font-display text-lg font-semibold text-foreground">
          Aurum Pharma
        </span>
      )}
    </span>
  );
}
