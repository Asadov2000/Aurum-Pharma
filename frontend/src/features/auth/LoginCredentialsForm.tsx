import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label } from "@/components/ui";
import { describeIdentityError } from "./identityErrors";
import { codeRetryAfter } from "./accountSecurityErrors";
import { requestLoginCode, verifyLoginCode } from "./api";
import {
  loginWithPassword,
  requestPhoneLoginCode,
  verifyPhoneLoginCode,
  type ContactChannel,
} from "./identityApi";
import { emailIdentitySchema, oneTimeCodeSchema, phoneIdentitySchema } from "./identityForms";
import { LoginMethodSwitch } from "./LoginMethodSwitch";
import { type LoginVerifyResponse } from "./types";
import { useLoginMethodsQuery } from "./identityQueries";

const PasswordResetForm = lazy(async () => {
  const module = await import("./PasswordResetForm");
  return { default: module.PasswordResetForm };
});

const credentialsSchema = z.object({
  email: emailIdentitySchema,
  password: z.string().min(1, "Введите пароль").max(128, "Не более 128 символов"),
});

type Fields = { email: string; phone: string; password: string; code: string };

export function LoginCredentialsForm({
  onAuthenticated,
}: {
  onAuthenticated: (result: LoginVerifyResponse) => Promise<void>;
}): JSX.Element {
  const [channel, setChannel] = useState<ContactChannel>("email");
  const [bootstrap, setBootstrap] = useState(false);
  const [reset, setReset] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [contact, setContact] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [retrySeconds, setRetrySeconds] = useState(0);
  const [devCode, setDevCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resending, setResending] = useState(false);
  const activeRequest = useRef<AbortController | null>(null);
  const form = useForm<Fields>({
    defaultValues: { email: "", phone: "+992", password: "", code: "" },
  });
  const busy = form.formState.isSubmitting || resending;
  const methods = useLoginMethodsQuery();
  const phoneAvailable = methods.data?.phone_login_enabled === true;

  useEffect(() => () => activeRequest.current?.abort(), []);
  useEffect(() => {
    if (retrySeconds <= 0) return;
    const timer = window.setTimeout(
      () => setRetrySeconds((value) => Math.max(0, value - 1)),
      1_000,
    );
    return () => window.clearTimeout(timer);
  }, [retrySeconds]);

  const changeMode = (nextChannel: ContactChannel, firstLogin = false) => {
    if (activeRequest.current) return;
    setChannel(nextChannel);
    setBootstrap(firstLogin);
    setCodeSent(false);
    setContact("");
    setDevCode(null);
    setError(null);
    setShowPassword(false);
    form.setValue("password", "");
    form.setValue("code", "");
    form.clearErrors();
  };

  const submit = form.handleSubmit(async (values) => {
    if (activeRequest.current) return;
    if (channel === "phone" && !phoneAvailable) return;
    form.clearErrors();
    setError(null);
    const identity =
      channel === "phone"
        ? phoneIdentitySchema.safeParse(values.phone)
        : emailIdentitySchema.safeParse(values.email);
    if (!identity.success) {
      form.setError(channel, { message: identity.error.issues[0]?.message });
      return;
    }
    if (channel === "email" && !bootstrap) {
      const parsed = credentialsSchema.safeParse(values);
      if (!parsed.success) {
        for (const issue of parsed.error.issues) {
          const field = issue.path[0];
          if (field === "email" || field === "password")
            form.setError(field, { message: issue.message });
        }
        return;
      }
    } else if (codeSent) {
      const parsed = oneTimeCodeSchema.safeParse(values.code);
      if (!parsed.success) {
        form.setError("code", { message: parsed.error.issues[0]?.message });
        return;
      }
    } else if (retrySeconds > 0) return;

    const controller = new AbortController();
    activeRequest.current = controller;
    try {
      if (channel === "email" && !bootstrap) {
        const result = await loginWithPassword(
          { email: identity.data, password: values.password },
          controller.signal,
        );
        if (!controller.signal.aborted) await onAuthenticated(result);
      } else if (!codeSent) {
        const response =
          channel === "phone"
            ? await requestPhoneLoginCode(identity.data, controller.signal)
            : await requestLoginCode({ email: identity.data }, controller.signal);
        if (controller.signal.aborted) return;
        setContact(identity.data);
        setCodeSent(true);
        setRetrySeconds(60);
        setDevCode(response.dev_code);
        form.setValue("code", response.dev_code ?? "");
      } else {
        const result =
          channel === "phone"
            ? await verifyPhoneLoginCode(
                { phone: contact, code: values.code.trim() },
                controller.signal,
              )
            : await verifyLoginCode(
                { email: contact, code: values.code.trim() },
                controller.signal,
              );
        if (!controller.signal.aborted) await onAuthenticated(result);
      }
    } catch (err) {
      if (!controller.signal.aborted)
        setError(
          describeIdentityError(
            err,
            "Не удалось выполнить вход. Проверьте соединение и повторите.",
          ),
        );
      const retryAfter = codeRetryAfter(err);
      if (!controller.signal.aborted && retryAfter) setRetrySeconds(retryAfter);
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
    }
  });

  const resendCode = async () => {
    if (activeRequest.current || retrySeconds > 0 || (channel === "phone" && !phoneAvailable))
      return;
    const controller = new AbortController();
    activeRequest.current = controller;
    setResending(true);
    setError(null);
    try {
      const response =
        channel === "phone"
          ? await requestPhoneLoginCode(contact, controller.signal)
          : await requestLoginCode({ email: contact }, controller.signal);
      if (controller.signal.aborted) return;
      form.setValue("code", response.dev_code ?? "");
      setDevCode(response.dev_code);
      setRetrySeconds(60);
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(describeIdentityError(err, "Не удалось отправить новый код."));
        const retryAfter = codeRetryAfter(err);
        if (retryAfter) setRetrySeconds(retryAfter);
      }
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
      if (!controller.signal.aborted) setResending(false);
    }
  };

  if (reset)
    return (
      <Suspense
        fallback={
          <p role="status" className="text-sm text-foreground-secondary">
            Открываем восстановление пароля…
          </p>
        }
      >
        <PasswordResetForm initialChannel={channel} onBack={() => setReset(false)} />
      </Suspense>
    );

  return (
    <>
      <header className="mb-7">
        <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-primary">
          Добро пожаловать
        </p>
        <h1 className="font-display text-[29px] font-semibold leading-tight tracking-tight text-foreground sm:text-[34px]">
          {codeSent ? "Подтверждение входа" : bootstrap ? "Первый вход" : "Вход в систему"}
        </h1>
        <p className="mt-2 break-words text-sm leading-6 text-foreground-secondary">
          {codeSent
            ? `Если контакт доступен для этого способа входа, код отправлен на ${contact}.`
            : bootstrap
              ? "Подтвердите почту, чтобы войти впервые и создать пароль."
              : channel === "phone"
                ? "Получите код на телефон, подтверждённый в настройках аккаунта."
                : "Введите почту и пароль вашего аккаунта."}
        </p>
      </header>
      {!bootstrap && !codeSent && (
        <LoginMethodSwitch
          value={channel}
          onChange={(value) => changeMode(value)}
          disabled={busy}
        />
      )}
      {channel === "phone" && !phoneAvailable && (
        <p
          role="status"
          className="mb-4 rounded-lg bg-foreground/5 p-3 text-sm text-foreground-secondary"
        >
          {methods.isPending
            ? "Проверяем доступность SMS…"
            : methods.isError
              ? "Не удалось проверить доступность SMS. Повторите позже или войдите по почте."
              : "Вход по SMS пока не подключён. Используйте почту."}
        </p>
      )}
      <form onSubmit={submit} noValidate className="space-y-5">
        {!codeSent ? (
          <>
            <div>
              <Label htmlFor="login-identity">
                {channel === "email" ? "Email" : "Номер телефона"}
              </Label>
              <Input
                key={channel}
                id="login-identity"
                type={channel === "email" ? "email" : "tel"}
                autoComplete={channel === "email" ? "username" : "tel"}
                autoFocus
                placeholder={channel === "email" ? "name@pharmacy.tj" : "+992 90 123 45 67"}
                invalid={Boolean(form.formState.errors[channel])}
                aria-describedby={form.formState.errors[channel] ? "identity-error" : undefined}
                {...form.register(channel)}
              />
              <FormError id="identity-error">{form.formState.errors[channel]?.message}</FormError>
            </div>
            {channel === "email" && !bootstrap && (
              <div className="relative">
                <div className="flex min-h-11 items-center pr-32">
                  <Label htmlFor="login-password">Пароль</Label>
                </div>
                <div className="relative">
                  <Input
                    id="login-password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    maxLength={128}
                    className="pr-24"
                    invalid={Boolean(form.formState.errors.password)}
                    aria-describedby={form.formState.errors.password ? "password-error" : undefined}
                    {...form.register("password")}
                  />
                  <button
                    type="button"
                    className="absolute inset-y-1 right-1 rounded px-3 text-xs font-semibold text-foreground-secondary"
                    aria-pressed={showPassword}
                    onClick={() => setShowPassword((value) => !value)}
                  >
                    {showPassword ? "Скрыть" : "Показать"}
                  </button>
                </div>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    form.setValue("password", "");
                    setReset(true);
                  }}
                  className="absolute right-0 top-0 min-h-11 rounded px-1 text-xs font-medium text-primary"
                >
                  Забыли пароль?
                </button>
                <FormError id="password-error">{form.formState.errors.password?.message}</FormError>
              </div>
            )}
          </>
        ) : (
          <>
            {devCode && (
              <p className="rounded-md bg-warning-subtle p-3 text-sm text-warning-foreground">
                Dev-режим: код {devCode} уже подставлен.
              </p>
            )}
            <div>
              <Label htmlFor="login-code">
                {channel === "phone" ? "Код из SMS" : "Код из письма"}
              </Label>
              <Input
                id="login-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                autoFocus
                className="text-center font-mono tracking-[0.3em]"
                invalid={Boolean(form.formState.errors.code)}
                aria-describedby={form.formState.errors.code ? "login-code-error" : undefined}
                {...form.register("code")}
              />
              <FormError id="login-code-error">{form.formState.errors.code?.message}</FormError>
            </div>
          </>
        )}
        {error && (
          <p
            role="alert"
            className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          >
            {error}
          </p>
        )}
        <Button
          type="submit"
          size="lg"
          className="w-full"
          isLoading={busy}
          disabled={
            (channel === "phone" && !phoneAvailable) ||
            (!codeSent && (bootstrap || channel === "phone") && retrySeconds > 0)
          }
        >
          {codeSent || (channel === "email" && !bootstrap)
            ? "Войти"
            : retrySeconds > 0
              ? `Повторить через ${retrySeconds} с`
              : "Получить код"}
        </Button>
      </form>
      <div className="mt-6 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() =>
            codeSent ? changeMode(channel, bootstrap) : changeMode("email", !bootstrap)
          }
        >
          {codeSent
            ? "Изменить данные"
            : bootstrap
              ? "Вернуться ко входу"
              : "Первый вход без пароля"}
        </Button>
        {codeSent && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={busy || retrySeconds > 0}
            onClick={() => void resendCode()}
          >
            {retrySeconds > 0 ? `Повторить через ${retrySeconds} с` : "Запросить новый код"}
          </Button>
        )}
      </div>
    </>
  );
}
