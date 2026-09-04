import { useConnectivity } from "@/lib/connectivityContext";
import { cn } from "@/lib/utils";

export function ConnectivityStatusBanner(): JSX.Element | null {
  const { status, checkNow } = useConnectivity();

  const serverUnavailable = status === "server-unavailable";
  if (status !== "offline" && !serverUnavailable) return null;

  return (
    <div
      aria-live="polite"
      className={cn(
        "border-b px-4 py-2 text-sm font-semibold sm:px-6",
        serverUnavailable
          ? "border-danger/30 bg-danger-subtle text-danger-foreground"
          : "border-warning/30 bg-warning-subtle text-warning-foreground",
      )}
      role="status"
    >
      {serverUnavailable ? (
        <>
          Сервер недоступен. Чек сохранён, операции заблокированы.
          <button className="ml-2 underline" onClick={checkNow} type="button">
            Проверить
          </button>
        </>
      ) : (
        "Нет интернета. Новые операции заблокированы."
      )}
    </div>
  );
}
