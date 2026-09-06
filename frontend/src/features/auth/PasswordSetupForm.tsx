import { useEffect, useState } from "react";
import { isAxiosError } from "axios";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label } from "@/components/ui";
import { describeAccountSecurityError } from "./accountSecurityErrors";

import { requestPasswordSetupCode, setupAccountPassword } from "./accountSecurityApi";

const schema = z
  .object({
    code: z.string().regex(/^\d{6}$/, "Введите 6 цифр из письма"),
    new_password: z
      .string()
      .min(12, "Используйте не менее 12 символов")
      .max(128, "Не более 128 символов"),
    confirmation: z.string(),
  })
  .refine((value) => value.new_password === value.confirmation, {
    path: ["confirmation"],
    message: "Пароли не совпадают",
  });

export function PasswordSetupForm({ onComplete }: { onComplete: () => void }): JSX.Element {
  const [sent, setSent] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retrySeconds, setRetrySeconds] = useState(0);
  useEffect(() => {
    if (retrySeconds <= 0) return;
    const timer = window.setTimeout(
      () => setRetrySeconds((value) => Math.max(0, value - 1)),
      1_000,
    );
    return () => window.clearTimeout(timer);
  }, [retrySeconds]);
  const form = useForm<z.infer<typeof schema>>({
    defaultValues: { code: "", new_password: "", confirmation: "" },
  });
  const requestCode = async () => {
    setError(null);
    setSending(true);
    try {
      const response = await requestPasswordSetupCode();
      if (response.dev_code) form.setValue("code", response.dev_code);
      setSent(true);
      setRetrySeconds(60);
    } catch (err) {
      if (isAxiosError<{ error?: { message?: string } }>(err) && err.response?.status === 429) {
        const retryAfter = Number(err.response.headers["retry-after"]);
        const fallbackSeconds =
          err.response.data.error?.message === "Too many code requests. Try again in an hour."
            ? 3_600
            : 60;
        const seconds =
          Number.isFinite(retryAfter) && retryAfter > 0 ? Math.ceil(retryAfter) : fallbackSeconds;
        setRetrySeconds(seconds);
        setError(
          "Код недавно запрашивали. Подождите до повторной отправки; вход в аккаунт остаётся доступным.",
        );
      } else {
        setError(describeAccountSecurityError(err, "Не удалось отправить код"));
      }
    } finally {
      setSending(false);
    }
  };
  const submit = form.handleSubmit(async (values) => {
    form.clearErrors();
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (field === "code" || field === "new_password" || field === "confirmation") {
          form.setError(field, { message: issue.message });
        }
      }
      return;
    }
    setError(null);
    try {
      await setupAccountPassword({
        code: parsed.data.code,
        new_password: parsed.data.new_password,
      });
      form.reset();
      onComplete();
    } catch (err) {
      setError(describeAccountSecurityError(err, "Не удалось сохранить пароль"));
    }
  });
  return (
    <div className="space-y-4">
      <p className="text-sm text-foreground-secondary">
        Сначала создайте пароль аккаунта. Он нужен для изменения защиты и подтверждения важных
        действий.
      </p>
      {!sent ? (
        <Button
          type="button"
          isLoading={sending}
          disabled={retrySeconds > 0}
          onClick={() => void requestCode()}
        >
          {retrySeconds > 0
            ? `Повторить через ${retrySeconds} с`
            : "Получить код для создания пароля"}
        </Button>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-4">
          <p className="text-sm text-foreground-secondary">
            Код отправлен на почту вашего аккаунта.
          </p>
          <div>
            <Label htmlFor="password-setup-code">Код из письма</Label>
            <Input
              id="password-setup-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              {...form.register("code")}
            />
            <FormError>{form.formState.errors.code?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="password-setup-new">Новый пароль</Label>
            <Input
              id="password-setup-new"
              type="password"
              autoComplete="new-password"
              maxLength={128}
              {...form.register("new_password")}
            />
            <FormError>{form.formState.errors.new_password?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="password-setup-confirm">Повторите пароль</Label>
            <Input
              id="password-setup-confirm"
              type="password"
              autoComplete="new-password"
              maxLength={128}
              {...form.register("confirmation")}
            />
            <FormError>{form.formState.errors.confirmation?.message}</FormError>
          </div>
          <Button type="submit" isLoading={form.formState.isSubmitting}>
            Сохранить пароль
          </Button>
          <Button
            type="button"
            variant="secondary"
            isLoading={sending}
            disabled={retrySeconds > 0 || form.formState.isSubmitting}
            onClick={() => void requestCode()}
          >
            {retrySeconds > 0 ? `Повторить через ${retrySeconds} с` : "Отправить код повторно"}
          </Button>
        </form>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
