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
  const asset = showName ? "logo" : "mark";
  return (
    <span
      className={cn(
        "brand-mark inline-flex min-w-0 max-w-full items-center",
        showName ? "w-[220px]" : "h-9 w-9 shrink-0",
        className,
      )}
      data-brand-tone={tone}
    >
      <img
        alt=""
        src={`/brand/v12-8/${asset}-color.svg`}
        width={showName ? 1061 : 256}
        height={256}
        className="brand-mark-color w-full"
      />
      <img
        alt=""
        src={`/brand/v12-8/${asset}-white.svg`}
        width={showName ? 1061 : 256}
        height={256}
        className="brand-mark-white w-full"
      />
      <span className="sr-only">Aurum Pharma</span>
    </span>
  );
}
