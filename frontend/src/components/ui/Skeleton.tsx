import { type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** A single shimmering placeholder block. */
export function Skeleton({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return <div className={cn("skeleton", className)} {...rest} />;
}

/** N stacked skeleton lines — a quick stand-in for a loading list/table. */
export function SkeletonRows({
  rows = 5,
  className,
}: {
  rows?: number;
  className?: string;
}): JSX.Element {
  return (
    <div className={cn("space-y-2", className)} aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}
