import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui";
import { dismissPasswordPrompt } from "./identityApi";
import {
  accountIdentityQueryKey,
  useAccountIdentityQuery,
  useLoginMethodsQuery,
} from "./identityQueries";
import { mfaSettingsQueryKey } from "./accountSecurityQueries";
import { PasswordSetupForm } from "./PasswordSetupForm";

export function PasswordSetupPrompt(): JSX.Element | null {
  const [expanded, setExpanded] = useState(false);
  const identity = useAccountIdentityQuery();
  const methods = useLoginMethodsQuery();
  const client = useQueryClient();
  const dismiss = useMutation({
    mutationFn: dismissPasswordPrompt,
    onSuccess: () => client.invalidateQueries({ queryKey: accountIdentityQueryKey }),
  });
  if (!identity.data?.password_prompt_pending || identity.data.has_password) return null;
  return (
    <section
      aria-label="Предложение создать пароль"
      className="mb-4 space-y-4 rounded-lg border border-primary/20 bg-surface p-4"
    >
      <div>
        <h2 className="font-medium text-foreground">Создайте пароль аккаунта</h2>
        <p className="mt-1 text-sm text-foreground-secondary">
          Он позволит входить по почте и подтверждать важные действия. Пароль можно создать сейчас
          или позже в настройках.
        </p>
      </div>
      {expanded ? (
        <PasswordSetupForm
          phoneAvailable={
            identity.data.phone_verified && methods.data?.phone_login_enabled === true
          }
          onComplete={() => {
            void client.invalidateQueries({ queryKey: accountIdentityQueryKey });
            void client.invalidateQueries({ queryKey: mfaSettingsQueryKey });
          }}
        />
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button type="button" onClick={() => setExpanded(true)}>
            Создать пароль
          </Button>
          <Button
            type="button"
            variant="secondary"
            isLoading={dismiss.isPending}
            onClick={() => dismiss.mutate()}
          >
            Позже
          </Button>
        </div>
      )}
      {dismiss.isError && (
        <p role="alert" className="text-sm text-danger">
          Не удалось сохранить выбор. Повторите позже.
        </p>
      )}
    </section>
  );
}
