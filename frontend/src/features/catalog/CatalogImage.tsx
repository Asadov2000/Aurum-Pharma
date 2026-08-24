import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

import { useCatalogImageQuery } from "./mediaQueries";
import { type CatalogItem } from "./types";

interface CatalogImageProps {
  item: CatalogItem;
  variant?: "thumbnail" | "detail";
  className?: string;
}

export function CatalogImage({
  item,
  variant = "thumbnail",
  className,
}: CatalogImageProps): JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(() => typeof IntersectionObserver === "undefined");
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    if (visible || !item.image_version || !hostRef.current) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "160px" },
    );
    observer.observe(hostRef.current);
    return () => observer.disconnect();
  }, [item.image_version, visible]);

  const imageQuery = useCatalogImageQuery(
    item.id,
    item.image_version,
    variant === "detail" ? "display" : "thumbnail",
    visible,
  );

  useEffect(() => {
    if (!imageQuery.data) {
      setObjectUrl(null);
      return;
    }
    const nextUrl = URL.createObjectURL(imageQuery.data);
    setObjectUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [imageQuery.data]);

  const sizeClass = variant === "detail" ? "h-44 w-full" : "h-12 w-16 shrink-0";

  return (
    <div
      ref={hostRef}
      className={cn(
        "relative grid place-items-center overflow-hidden rounded-md border border-border bg-background",
        sizeClass,
        className,
      )}
    >
      {objectUrl ? (
        <img
          src={objectUrl}
          alt={`Упаковка ${item.brand_name}`}
          className="h-full w-full object-contain p-1"
        />
      ) : (
        <div className="grid h-full w-full place-items-center bg-primary/5" aria-hidden="true">
          <span className="rounded border border-primary/25 bg-surface px-1.5 py-1 text-[10px] font-bold text-primary">
            Rx
          </span>
        </div>
      )}
      {imageQuery.isFetching && !objectUrl && item.image_version && (
        <span className="absolute inset-x-2 bottom-1 h-0.5 animate-pulse rounded bg-primary/30" />
      )}
    </div>
  );
}
