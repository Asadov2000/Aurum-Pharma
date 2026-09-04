import { useConnectivity } from "@/lib/connectivityContext";

export function ServerStatusBanner(): JSX.Element | null {
  const { status, checkNow } = useConnectivity();
  if (status !== "server-unavailable") {
    return null;
  }

  return (
    <div
      aria-live="polite"
      className="border-b border-danger/30 bg-danger-subtle px-4 py-2 text-sm font-semibold text-danger-foreground sm:px-6"
      data-testid="server-status-banner"
      role="status"
    >
      <span>Сервер недоступен. Текущий чек сохранён. Новые операции заблокированы.</span>
      <button className="ml-2 underline" onClick={checkNow} type="button">
        Проверить
      </button>
    </div>
  );
}
