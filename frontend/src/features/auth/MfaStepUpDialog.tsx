import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Modal } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";
import { useAuthStore } from "@/stores/auth";

import { stepUpMfa } from "./api";
import { cancelMfaStepUp, completeMfaStepUp, useMfaStepUpRequested } from "./stepUpCoordinator";

const schema = z.object({
  code: z.string().regex(/^\d{6}$/, "Код состоит из 6 цифр"),
});

type StepUpForm = z.infer<typeof schema>;

export function MfaStepUpDialog(): JSX.Element {
  const open = useMfaStepUpRequested();
  const setTokens = useAuthStore((state) => state.setTokens);
  const [requestError, setRequestError] = useState<string | null>(null);
  const form = useForm<StepUpForm>({ defaultValues: { code: "" } });

  useEffect(() => {
    if (!open) return;
    form.reset({ code: "" });
    setRequestError(null);
  }, [form, open]);

  useEffect(() => () => cancelMfaStepUp(), []);

  const close = () => {
    if (form.formState.isSubmitting) return;
    cancelMfaStepUp();
  };

  const submit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      form.setError("code", {
        message: parsed.error.issues[0]?.message ?? "Проверьте код",
      });
      return;
    }

    setRequestError(null);
    try {
      const tokens = await stepUpMfa(parsed.data.code);
      setTokens(tokens);
      completeMfaStepUp(tokens.access_token);
    } catch (error) {
      setRequestError(
        describeApiError(error, "Не удалось подтвердить действие. Попробуйте ещё раз."),
      );
    }
  });

  return (
    <Modal open={open} onClose={close} title="Подтверждение действия">
      <form onSubmit={submit} noValidate className="space-y-4">
        <p className="text-sm text-foreground-secondary">
          Введите текущий код из приложения-аутентификатора.
        </p>
        <div>
          <Label htmlFor="mfa-step-up-code">Код подтверждения</Label>
          <Input
            id="mfa-step-up-code"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            autoFocus
            invalid={Boolean(form.formState.errors.code)}
            {...form.register("code")}
          />
          <FormError>{form.formState.errors.code?.message}</FormError>
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
    </Modal>
  );
}
