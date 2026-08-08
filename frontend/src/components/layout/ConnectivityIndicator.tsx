import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

export function ConnectivityIndicator(): JSX.Element {
  const [isOnline, setIsOnline] = useState(readOnlineStatus);

  useEffect(() => {
    const update = () => setIsOnline(readOnlineStatus());
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  const label = isOnline ? "Онлайн" : "Нет сети";

  return (
    <span
      role="status"
      aria-live="polite"
      className="hidden min-h-9 items-center gap-2 rounded-md px-2 text-xs font-medium text-foreground-secondary sm:inline-flex"
      title={label}
    >
      <span
        aria-hidden="true"
        className={cn("h-2 w-2 rounded-full", isOnline ? "bg-success" : "bg-danger")}
      />
      <span className="sr-only xl:not-sr-only">{label}</span>
    </span>
  );
}

function readOnlineStatus(): boolean {
  return typeof navigator === "undefined" ? true : navigator.onLine;
}
