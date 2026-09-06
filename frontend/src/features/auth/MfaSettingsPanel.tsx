import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Checkbox,
  FormError,
  Input,
  Label,
} from "@/components/ui";
import { describeAccountSecurityError } from "./accountSecurityErrors";
import { useAuthStore } from "@/stores/auth";

import {
  confirmAccountMfaEnrollment,
  disableAccountMfa,
  startAccountMfaEnrollment,
} from "./accountSecurityApi";
import { PasswordSetupForm } from "./PasswordSetupForm";
import { activeSessionsQueryKey } from "./queries";
import { mfaSettingsQueryKey, useMfaSettingsQuery } from "./accountSecurityQueries";
import { type MfaSettingsEnrollment, type TokenPair } from "./types";

const passwordSchema = z.object({
  password: z.string().min(1, "Введите пароль").max(1024, "Не более 1024 символов"),
});
const enrollmentSchema = z.object({
  code: z.string().regex(/^\d{6}$/, "Код состоит из 6 цифр"),
  saved: z.boolean().refine(Boolean, "Сохраните резервные коды перед включением защиты"),
});

export function MfaSettingsPanel(): JSX.Element {
  const settings = useMfaSettingsQuery();
  const client = useQueryClient();
  const [mode, setMode] = useState<"idle" | "enroll" | "disable">("idle");
  const [success, setSuccess] = useState<string | null>(null);
  const completed = (tokens: TokenPair, enabled: boolean) => {
    useAuthStore.getState().setTokens(tokens);
    client.setQueryData(mfaSettingsQueryKey, {
      enabled,
      prompt_pending: false,
      has_password: true,
    });
    void client.invalidateQueries({ queryKey: activeSessionsQueryKey });
    setMode("idle");
    setSuccess(enabled ? "Двухфакторная защита включена." : "Двухфакторная защита выключена.");
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>Двухфакторная защита</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-foreground-secondary">
          Вы сами выбираете, нужен ли дополнительный код из приложения при входе. Защиту можно
          включить и выключить здесь.
        </p>
        {settings.isLoading ? (
          <p role="status">Загрузка настроек защиты…</p>
        ) : settings.error ? (
          <div className="space-y-2">
            <p role="alert" className="text-sm text-danger">
              Не удалось загрузить настройки защиты.
            </p>
            <Button type="button" variant="secondary" onClick={() => void settings.refetch()}>
              Повторить загрузку защиты
            </Button>
          </div>
        ) : settings.data ? (
          <>
            <Badge tone={settings.data.enabled ? "success" : "neutral"}>
              {settings.data.enabled ? "Включена" : "Выключена"}
            </Badge>
            {success && (
              <p role="status" className="text-sm text-success-foreground">
                {success}
              </p>
            )}
            {!settings.data.has_password ? (
              <PasswordSetupForm
                onComplete={() => {
                  setSuccess("Пароль сохранён. Теперь можно настроить защиту.");
                  void client.invalidateQueries({ queryKey: mfaSettingsQueryKey });
                }}
              />
            ) : mode === "enroll" ? (
              <EnrollmentForm
                onCancel={() => setMode("idle")}
                onComplete={(tokens) => completed(tokens, true)}
              />
            ) : mode === "disable" ? (
              <DisableForm
                onCancel={() => setMode("idle")}
                onComplete={(tokens) => completed(tokens, false)}
              />
            ) : (
              <Button
                type="button"
                variant={settings.data.enabled ? "secondary" : "primary"}
                onClick={() => {
                  setSuccess(null);
                  setMode(settings.data.enabled ? "disable" : "enroll");
                }}
              >
                {settings.data.enabled ? "Выключить защиту" : "Включить защиту"}
              </Button>
            )}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

function EnrollmentForm({
  onCancel,
  onComplete,
}: {
  onCancel: () => void;
  onComplete: (tokens: TokenPair) => void;
}): JSX.Element {
  const [setup, setSetup] = useState<MfaSettingsEnrollment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const password = useForm<z.infer<typeof passwordSchema>>({ defaultValues: { password: "" } });
  const enrollment = useForm<z.infer<typeof enrollmentSchema>>({
    defaultValues: { code: "", saved: false },
  });
  const start = password.handleSubmit(async (values) => {
    const parsed = passwordSchema.safeParse(values);
    if (!parsed.success) {
      password.setError("password", { message: parsed.error.issues[0]?.message });
      return;
    }
    setError(null);
    try {
      setSetup(await startAccountMfaEnrollment(parsed.data.password));
      password.reset();
    } catch (err) {
      setError(describeAccountSecurityError(err, "Не удалось начать подключение защиты"));
    }
  });
  const confirm = enrollment.handleSubmit(async (values) => {
    enrollment.clearErrors();
    const parsed = enrollmentSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (field === "code" || field === "saved")
          enrollment.setError(field, { message: issue.message });
      }
      return;
    }
    if (!setup) return;
    setError(null);
    const requestAccessToken = useAuthStore.getState().accessToken;
    try {
      const tokens = await confirmAccountMfaEnrollment({
        challenge_token: setup.challenge_token,
        code: parsed.data.code,
      });
      if (useAuthStore.getState().accessToken !== requestAccessToken) return;
      setSetup(null);
      enrollment.reset();
      onComplete(tokens);
    } catch (err) {
      setError(
        describeAccountSecurityError(
          err,
          "Не удалось включить защиту. Проверьте код или начните настройку заново.",
        ),
      );
    }
  });
  const busy = password.formState.isSubmitting || enrollment.formState.isSubmitting;
  return (
    <div className="space-y-4">
      {!setup ? (
        <form onSubmit={start} noValidate className="space-y-4">
          <p className="text-sm text-foreground-secondary">
            Подтвердите пароль, затем добавьте аккаунт в приложение-аутентификатор.
          </p>
          <div>
            <Label htmlFor="mfa-enroll-password">Пароль аккаунта</Label>
            <Input
              id="mfa-enroll-password"
              type="password"
              autoComplete="current-password"
              maxLength={1024}
              {...password.register("password")}
            />
            <FormError>{password.formState.errors.password?.message}</FormError>
          </div>
          <Button type="submit" isLoading={busy}>
            Продолжить настройку
          </Button>
        </form>
      ) : (
        <form onSubmit={confirm} noValidate className="space-y-4">
          <p className="text-sm text-foreground-secondary">
            В приложении-аутентификаторе выберите добавление по ключу настройки и введите этот
            секретный ключ. Никому его не передавайте.
          </p>
          <div>
            <Label>Секретный ключ</Label>
            <code className="mt-1 block select-all break-all rounded-md border border-border p-3 font-mono text-sm">
              {setup.secret}
            </code>
          </div>
          <div>
            <Label>Резервные коды</Label>
            <p className="mb-2 text-sm text-foreground-secondary">
              Сохраните их в безопасном месте отдельно от телефона. Каждый код можно использовать
              только один раз.
            </p>
            <div
              role="group"
              aria-label="Резервные коды"
              className="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-2"
            >
              {setup.recovery_codes.map((code) => (
                <code key={code} className="select-all break-all font-mono text-sm">
                  {code}
                </code>
              ))}
            </div>
          </div>
          <label className="flex items-start gap-2 text-sm">
            <Checkbox {...enrollment.register("saved")} />
            <span>Я сохранил резервные коды в безопасном месте</span>
          </label>
          <FormError>{enrollment.formState.errors.saved?.message}</FormError>
          <div>
            <Label htmlFor="mfa-settings-code">Код из приложения</Label>
            <Input
              id="mfa-settings-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              {...enrollment.register("code")}
            />
            <FormError>{enrollment.formState.errors.code?.message}</FormError>
          </div>
          <p className="text-sm text-foreground-secondary">
            После включения другие сеансы завершатся. Текущий останется открыт.
          </p>
          <Button type="submit" isLoading={busy}>
            Подтвердить и включить
          </Button>
        </form>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <Button type="button" variant="secondary" disabled={busy} onClick={onCancel}>
        Отмена
      </Button>
    </div>
  );
}

function DisableForm({
  onCancel,
  onComplete,
}: {
  onCancel: () => void;
  onComplete: (tokens: TokenPair) => void;
}): JSX.Element {
  const [error, setError] = useState<string | null>(null);
  const form = useForm<z.infer<typeof passwordSchema>>({
    defaultValues: { password: "" },
  });
  const submit = form.handleSubmit(async (values) => {
    form.clearErrors();
    const parsed = passwordSchema.safeParse(values);
    if (!parsed.success) {
      form.setError("password", { message: parsed.error.issues[0]?.message });
      return;
    }
    setError(null);
    const requestAccessToken = useAuthStore.getState().accessToken;
    try {
      const tokens = await disableAccountMfa({
        password: parsed.data.password,
      });
      if (useAuthStore.getState().accessToken !== requestAccessToken) return;
      form.reset();
      onComplete(tokens);
    } catch (err) {
      setError(describeAccountSecurityError(err, "Не удалось выключить защиту. Проверьте пароль."));
    }
  });
  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <p className="text-sm text-foreground-secondary">
        После отключения вход не будет запрашивать код из приложения. Другие сеансы завершатся,
        резервные коды перестанут действовать.
      </p>
      <div>
        <Label htmlFor="mfa-disable-password">Пароль аккаунта</Label>
        <Input
          id="mfa-disable-password"
          type="password"
          autoComplete="current-password"
          maxLength={1024}
          {...form.register("password")}
        />
        <FormError>{form.formState.errors.password?.message}</FormError>
      </div>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" variant="danger" isLoading={form.formState.isSubmitting}>
          Подтвердить отключение
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={form.formState.isSubmitting}
          onClick={onCancel}
        >
          Отмена
        </Button>
      </div>
    </form>
  );
}
