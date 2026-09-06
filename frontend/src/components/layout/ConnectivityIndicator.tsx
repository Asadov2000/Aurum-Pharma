import { useConnectivity } from "@/lib/connectivityContext";
import { cn } from "@/lib/utils";

export function ConnectivityIndicator(): JSX.Element {
  const { status } = useConnectivity();
  const label =
    status === "online"
      ? "Онлайн"
      : status === "offline"
        ? "Нет сети"
        : status === "server-unavailable"
          ? "Сервер недоступен"
          : "Проверка связи";

  return (
    <span
      role="status"
      aria-live="polite"
      className="hidden min-h-9 items-center gap-2 rounded-md px-2 text-xs font-medium text-foreground-secondary sm:inline-flex"
      title={label}
    >
      <span
        aria-hidden="true"
        className={cn(
          "h-2 w-2 rounded-full",
          status === "online"
            ? "bg-success"
            : status === "checking"
              ? "bg-warning"
              : "bg-danger",
        )}
      />
      <span className="sr-only xl:not-sr-only">{label}</span>
    </span>
  );
}
