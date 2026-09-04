import { useConnectivity } from "@/lib/connectivityContext";

export function OfflineStatusBanner(): JSX.Element | null {
  const { status } = useConnectivity();
  if (status !== "offline") {
    return null;
  }

  return (
    <div
      aria-live="polite"
      className="border-b border-warning/30 bg-warning-subtle px-4 py-2 text-sm font-semibold text-warning-foreground sm:px-6"
      data-testid="offline-status-banner"
      role="status"
    >
      Нет связи. Касса работает только онлайн, операции будут доступны после
      восстановления интернета.
    </div>
  );
}
