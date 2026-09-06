import { Link } from "@tanstack/react-router";

import { Button } from "@/components/ui";
import { useAuthStore } from "@/stores/auth";

import { useDismissMfaPrompt, useMfaSettingsQuery } from "./accountSecurityQueries";

export function MfaSetupPrompt(): JSX.Element | null {
  const userId = useAuthStore((state) => state.user?.id);
  const settings = useMfaSettingsQuery(Boolean(userId));
  const dismiss = useDismissMfaPrompt();
  if (!userId || !settings.data?.prompt_pending || settings.data.enabled) return null;
  return (
    <section
      aria-label="Предложение защиты аккаунта"
      className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface p-4"
    >
      <div>
        <h2 className="font-medium text-foreground">Усилить защиту аккаунта?</h2>
        <p className="text-sm text-foreground-secondary">
          Двухфакторная защита добавляет код из приложения при входе. Подключение добровольное.
        </p>
        {dismiss.error && (
          <p className="text-sm text-danger" role="alert">
            Не удалось сохранить выбор. Можно продолжить работу и повторить позже.
          </p>
        )}
      </div>
      <div className="flex gap-2">
        <Link
          to="/settings"
          search={{ section: "security" }}
          className="rounded-md px-3 py-2 text-sm font-medium text-primary hover:bg-foreground/5"
          onClick={() => dismiss.mutate()}
        >
          Настроить
        </Link>
        <Button
          type="button"
          variant="secondary"
          isLoading={dismiss.isPending}
          onClick={() => dismiss.mutate()}
        >
          Пока пропустить
        </Button>
      </div>
    </section>
  );
}
