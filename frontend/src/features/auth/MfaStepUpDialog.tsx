import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { isAxiosError } from "axios";

import { Button, FormError, Input, Label, Modal } from "@/components/ui";
import { describeAccountSecurityError } from "./accountSecurityErrors";
import { useAuthStore } from "@/stores/auth";

import { confirmAccountPassword } from "./accountSecurityApi";
import { useMfaSettingsQuery } from "./accountSecurityQueries";
import { cancelMfaStepUp, completeMfaStepUp, useMfaStepUpRequested } from "./stepUpCoordinator";

const schema = z.object({
  password: z.string().min(1, "Введите пароль").max(1024, "Не более 1024 символов"),
});

type StepUpForm = z.infer<typeof schema>;

export function MfaStepUpDialog(): JSX.Element {
  const open = useMfaStepUpRequested();
  const settings = useMfaSettingsQuery(open);
  const setTokens = useAuthStore((state) => state.setTokens);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [needsPassword, setNeedsPassword] = useState(false);
  const form = useForm<StepUpForm>({ defaultValues: { password: "" } });

  useEffect(() => {
    if (!open) return;
    form.reset({ password: "" });
    setRequestError(null);
    setNeedsPassword(false);
  }, [form, open]);

  useEffect(() => () => cancelMfaStepUp(), []);

  const close = () => {
    if (form.formState.isSubmitting) return;
    cancelMfaStepUp();
  };

  const submit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      form.setError("password", {
        message: parsed.error.issues[0]?.message ?? "Проверьте пароль",
      });
      return;
    }

    setRequestError(null);
    const requestAccessToken = useAuthStore.getState().accessToken;
    try {
      const tokens = await confirmAccountPassword(parsed.data.password);
      if (useAuthStore.getState().accessToken !== requestAccessToken) return;
      setTokens(tokens);
      form.reset();
      completeMfaStepUp(tokens.access_token);
    } catch (error) {
      if (
        isAxiosError<{ error?: { details?: { reason?: string } } }>(error) &&
        error.response?.data.error?.details?.reason === "password_setup_required"
      ) {
        setNeedsPassword(true);
      }
      setRequestError(
        describeAccountSecurityError(error, "Не удалось подтвердить действие. Попробуйте ещё раз."),
      );
    }
  });

  return (
    <Modal open={open} onClose={close} title="Подтверждение действия">
      {needsPassword || settings.data?.has_password === false ? (
        <div className="space-y-4">
          <p className="text-sm text-foreground-secondary">
            Для подтверждения этого действия сначала создайте пароль аккаунта в настройках
            безопасности. После настройки вернитесь к действию.
          </p>
          <a
            href="/settings?section=security"
            onClick={close}
            className="block text-sm text-primary underline"
          >
            Создать пароль в настройках безопасности
          </a>
          <Button type="button" variant="secondary" onClick={close}>
            Отмена
          </Button>
        </div>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-4">
          <p className="text-sm text-foreground-secondary">
            Введите пароль аккаунта, чтобы подтвердить важное действие. Подтверждение действует 10
            минут.
          </p>
          <div>
            <Label htmlFor="password-step-up">Пароль аккаунта</Label>
            <Input
              id="password-step-up"
              type="password"
              autoComplete="current-password"
              maxLength={1024}
              autoFocus
              invalid={Boolean(form.formState.errors.password)}
              {...form.register("password")}
            />
            <FormError>{form.formState.errors.password?.message}</FormError>
          </div>
          {requestError && <p className="text-sm text-danger">{requestError}</p>}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              disabled={form.formState.isSubmitting}
              onClick={close}
            >
              Отмена
            </Button>
            <Button type="submit" isLoading={form.formState.isSubmitting}>
              Подтвердить
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}
