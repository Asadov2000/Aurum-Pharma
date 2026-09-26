import { useState, useSyncExternalStore } from "react";

import { Button } from "@/components/ui";

import { completePendingLogout } from "./cookieRequests";
import { readLogoutState, subscribeLogoutState } from "./logoutState";

function snapshot(): string {
  const state = readLogoutState();
  return state?.id === "blocked" ? "blocked" : (state?.phase ?? "");
}

export function LogoutNotice(): JSX.Element | null {
  const phase = useSyncExternalStore(subscribeLogoutState, snapshot);
  const [checking, setChecking] = useState(false);
  const [failed, setFailed] = useState(false);
  if (phase === "blocked") {
    return (
      <p role="alert" className="mb-5 rounded-lg border border-border p-3 text-sm">
        Браузер не может безопасно сохранить состояние входа. Обратитесь к администратору или
        используйте другое устройство.
      </p>
    );
  }
  if (phase !== "pending") return null;

  const retry = async () => {
    setChecking(true);
    setFailed(false);
    try {
      await completePendingLogout();
    } catch {
      setFailed(true);
    } finally {
      setChecking(false);
    }
  };

  return (
    <div
      role="status"
      className="mb-5 rounded-lg border border-border bg-surface-secondary p-3 text-sm"
    >
      <p>Доступ на этом устройстве закрыт. Выход с сервера ещё не подтверждён.</p>
      <p className="mt-1 text-foreground-secondary">
        Подключитесь к интернету. Перед новым входом мы завершим предыдущую сессию.
      </p>
      <Button
        type="button"
        variant="ghost"
        disabled={checking}
        onClick={() => void retry()}
        className="mt-2"
      >
        {checking ? "Проверяем соединение…" : "Повторить выход с сервера"}
      </Button>
      {failed && (
        <p className="mt-2 text-danger-foreground">
          Не удалось подтвердить выход. Проверьте соединение и используйте актуальный браузер через
          HTTPS или приложение.
        </p>
      )}
    </div>
  );
}
