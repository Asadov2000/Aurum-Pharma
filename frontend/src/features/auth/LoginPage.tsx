import { type FormEvent, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { z } from "zod";

import { Button, Checkbox, FormError, Input, Label } from "@/components/ui";
import { BrandMark } from "@/components/layout/BrandMark";
import { describeApiError } from "@/lib/errorMessages";
import { useAuthStore } from "@/stores/auth";

import {
  completeMfaEnrollment,
  recoverMfa,
  requestLoginCode,
  startMfaEnrollment,
  verifyMfa,
} from "./api";
import { useAuth } from "./hooks";
import { type MfaEnrollmentSetup } from "./types";

const emailSchema = z.object({
  email: z.string().min(1, "Введите email").email("Некорректный email"),
});

const codeSchema = z.object({
  code: z.string().regex(/^\d{6}$/, "Код состоит из 6 цифр"),
  password: z.string().optional(),
});

const mfaSchema = z.object({
  code: z.string().regex(/^\d{6}$/, "Код состоит из 6 цифр"),
});

const enrollmentSchema = mfaSchema.extend({
  saved: z.boolean().refine((value) => value, "Сохраните резервные коды"),
});

const recoverySchema = z.object({
  recoveryCode: z.string().min(20, "Введите резервный код").max(32, "Некорректный резервный код"),
});

type EmailForm = z.infer<typeof emailSchema>;
type CodeForm = z.infer<typeof codeSchema>;
type MfaForm = z.infer<typeof mfaSchema>;
type EnrollmentForm = z.infer<typeof enrollmentSchema>;
type RecoveryForm = z.infer<typeof recoverySchema>;
type LoginStep = "email" | "code" | "mfa" | "enroll" | "recovery";

const stepTitle: Record<LoginStep, string> = {
  email: "Вход в систему",
  code: "Подтверждение входа",
  mfa: "Двухфакторная защита",
  enroll: "Настройка защиты",
  recovery: "Восстановление доступа",
};

function extractErrorMessage(err: unknown): string {
  return describeApiError(err, "Не удалось выполнить запрос. Проверьте соединение.");
}

function LoginError({ message }: { message: string | null }): JSX.Element | null {
  if (!message) return null;
  return (
    <p
      className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm leading-5 text-danger-foreground"
      role="alert"
    >
      {message}
    </p>
  );
}

export function LoginPage(): JSX.Element {
  const [step, setStep] = useState<LoginStep>("email");
  const [email, setEmail] = useState("");
  const [devCode, setDevCode] = useState<string | null>(null);
  const [topError, setTopError] = useState<string | null>(null);
  const [challengeToken, setChallengeToken] = useState<string | null>(null);
  const [enrollment, setEnrollment] = useState<MfaEnrollmentSetup | null>(null);
  const [enrollmentLoading, setEnrollmentLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const activeRequestRef = useRef<AbortController | null>(null);
  const authFlowVersionRef = useRef(0);
  const { login } = useAuth();
  const setTokens = useAuthStore((state) => state.setTokens);
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { from?: string };

  const emailForm = useForm<EmailForm>({ defaultValues: { email: "" } });
  const codeForm = useForm<CodeForm>({ defaultValues: { code: "", password: "" } });
  const mfaForm = useForm<MfaForm>({ defaultValues: { code: "" } });
  const enrollmentForm = useForm<EnrollmentForm>({
    defaultValues: { code: "", saved: false },
  });
  const recoveryForm = useForm<RecoveryForm>({
    defaultValues: { recoveryCode: "" },
  });

  useEffect(
    () => () => {
      activeRequestRef.current?.abort();
    },
    [],
  );

  const startRequest = (): AbortController => {
    activeRequestRef.current?.abort();
    const controller = new AbortController();
    activeRequestRef.current = controller;
    return controller;
  };

  const isCurrentRequest = (controller: AbortController): boolean =>
    activeRequestRef.current === controller && !controller.signal.aborted;

  const finishRequest = (controller: AbortController): void => {
    if (activeRequestRef.current === controller) activeRequestRef.current = null;
  };

  const resetToEmail = (): void => {
    if (activeRequestRef.current) return;
    authFlowVersionRef.current += 1;
    setStep("email");
    setEmail("");
    setDevCode(null);
    setTopError(null);
    setChallengeToken(null);
    setEnrollment(null);
    setEnrollmentLoading(false);
    setShowPassword(false);
    emailForm.clearErrors();
    codeForm.reset({ code: "", password: "" });
    mfaForm.reset({ code: "" });
    enrollmentForm.reset({ code: "", saved: false });
    recoveryForm.reset({ recoveryCode: "" });
  };

  const navigateAfterLogin = () => {
    const target = typeof search.from === "string" && search.from !== "/login" ? search.from : "/";
    navigate({ to: target });
  };

  const beginEnrollment = async (token: string, controller: AbortController) => {
    setStep("enroll");
    setEnrollment(null);
    setEnrollmentLoading(true);
    try {
      const setup = await startMfaEnrollment({ challenge_token: token }, controller.signal);
      if (!isCurrentRequest(controller)) return;
      setEnrollment(setup);
      enrollmentForm.reset({ code: "", saved: false });
    } finally {
      if (isCurrentRequest(controller)) setEnrollmentLoading(false);
    }
  };

  const submitEmail = emailForm.handleSubmit(async (values) => {
    if (activeRequestRef.current) return;
    const parsed = emailSchema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const path = issue.path[0];
        if (typeof path !== "string" || seen.has(path)) continue;
        seen.add(path);
        emailForm.setError(path as keyof EmailForm, { message: issue.message });
      }
      return;
    }
    setTopError(null);
    setDevCode(null);
    setChallengeToken(null);
    setEnrollment(null);
    setShowPassword(false);
    codeForm.reset({ code: "", password: "" });
    const controller = startRequest();
    try {
      const res = await requestLoginCode({ email: parsed.data.email }, controller.signal);
      if (!isCurrentRequest(controller)) return;
      setEmail(parsed.data.email);
      if (res.dev_code) {
        setDevCode(res.dev_code);
        codeForm.setValue("code", res.dev_code);
      } else {
        setDevCode(null);
      }
      setStep("code");
    } catch (err) {
      if (!controller.signal.aborted) setTopError(extractErrorMessage(err));
    } finally {
      finishRequest(controller);
    }
  });

  const submitCodeValues = async (values: CodeForm, flowVersion: number) => {
    if (flowVersion !== authFlowVersionRef.current || activeRequestRef.current) return;
    const parsed = codeSchema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const path = issue.path[0];
        if (typeof path !== "string" || seen.has(path)) continue;
        seen.add(path);
        codeForm.setError(path as keyof CodeForm, { message: issue.message });
      }
      return;
    }
    setTopError(null);
    const controller = startRequest();
    try {
      const result = await login(
        {
          email,
          code: parsed.data.code,
          password: parsed.data.password ? parsed.data.password : undefined,
        },
        controller.signal,
      );
      if (!isCurrentRequest(controller)) return;
      if ("access_token" in result) {
        navigateAfterLogin();
        return;
      }
      setChallengeToken(result.challenge_token);
      if (result.status === "mfa_enrollment_required") {
        await beginEnrollment(result.challenge_token, controller);
      } else if (result.status === "mfa_recovery_required") {
        setStep("recovery");
      } else {
        setStep("mfa");
      }
    } catch (err) {
      if (!controller.signal.aborted) setTopError(extractErrorMessage(err));
    } finally {
      finishRequest(controller);
    }
  };

  const submitCode = (event: FormEvent<HTMLFormElement>): void => {
    const flowVersion = authFlowVersionRef.current;
    void codeForm.handleSubmit((values) => submitCodeValues(values, flowVersion))(event);
  };

  const submitMfaValues = async (values: MfaForm, flowVersion: number) => {
    if (flowVersion !== authFlowVersionRef.current || activeRequestRef.current) return;
    if (!challengeToken) return;
    const parsed = mfaSchema.safeParse(values);
    if (!parsed.success) {
      mfaForm.setError("code", {
        message: parsed.error.issues[0]?.message ?? "Проверьте код",
      });
      return;
    }
    setTopError(null);
    const controller = startRequest();
    try {
      const tokens = await verifyMfa(
        {
          challenge_token: challengeToken,
          code: parsed.data.code,
        },
        controller.signal,
      );
      if (!isCurrentRequest(controller)) return;
      setTokens(tokens);
      navigateAfterLogin();
    } catch (err) {
      if (!controller.signal.aborted) setTopError(extractErrorMessage(err));
    } finally {
      finishRequest(controller);
    }
  };

  const submitMfa = (event: FormEvent<HTMLFormElement>): void => {
    const flowVersion = authFlowVersionRef.current;
    void mfaForm.handleSubmit((values) => submitMfaValues(values, flowVersion))(event);
  };

  const submitEnrollmentValues = async (values: EnrollmentForm, flowVersion: number) => {
    if (flowVersion !== authFlowVersionRef.current || activeRequestRef.current) return;
    if (!challengeToken || !enrollment) return;
    const parsed = enrollmentSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (field === "code" || field === "saved") {
          enrollmentForm.setError(field, { message: issue.message });
        }
      }
      return;
    }
    setTopError(null);
    const controller = startRequest();
    try {
      const tokens = await completeMfaEnrollment(
        {
          challenge_token: challengeToken,
          code: parsed.data.code,
        },
        controller.signal,
      );
      if (!isCurrentRequest(controller)) return;
      setTokens(tokens);
      setEnrollment(null);
      navigateAfterLogin();
    } catch (err) {
      if (!controller.signal.aborted) setTopError(extractErrorMessage(err));
    } finally {
      finishRequest(controller);
    }
  };

  const submitEnrollment = (event: FormEvent<HTMLFormElement>): void => {
    const flowVersion = authFlowVersionRef.current;
    void enrollmentForm.handleSubmit((values) => submitEnrollmentValues(values, flowVersion))(
      event,
    );
  };

  const submitRecoveryValues = async (values: RecoveryForm, flowVersion: number) => {
    if (flowVersion !== authFlowVersionRef.current || activeRequestRef.current) return;
    if (!challengeToken) return;
    const parsed = recoverySchema.safeParse(values);
    if (!parsed.success) {
      recoveryForm.setError("recoveryCode", {
        message: parsed.error.issues[0]?.message ?? "Проверьте резервный код",
      });
      return;
    }
    setTopError(null);
    const controller = startRequest();
    try {
      const result = await recoverMfa(
        {
          challenge_token: challengeToken,
          recovery_code: parsed.data.recoveryCode,
        },
        controller.signal,
      );
      if (!isCurrentRequest(controller)) return;
      setChallengeToken(result.challenge_token);
      await beginEnrollment(result.challenge_token, controller);
    } catch (err) {
      if (!controller.signal.aborted) setTopError(extractErrorMessage(err));
    } finally {
      finishRequest(controller);
    }
  };

  const submitRecovery = (event: FormEvent<HTMLFormElement>): void => {
    const flowVersion = authFlowVersionRef.current;
    void recoveryForm.handleSubmit((values) => submitRecoveryValues(values, flowVersion))(event);
  };

  const authRequestBusy =
    codeForm.formState.isSubmitting ||
    mfaForm.formState.isSubmitting ||
    enrollmentForm.formState.isSubmitting ||
    recoveryForm.formState.isSubmitting ||
    enrollmentLoading;

  return (
    <main className="min-h-[100dvh] bg-background lg:grid lg:grid-cols-[minmax(26rem,0.92fr)_minmax(28rem,1.08fr)]">
      <section
        className="relative hidden min-h-[100dvh] overflow-hidden border-r border-border bg-surface lg:block lg:bg-[url('/assets/auth-pharmacy.webp')] lg:bg-cover lg:bg-center"
        aria-hidden="true"
      >
        <div className="absolute inset-0 bg-primary-950/10" aria-hidden="true" />
      </section>

      <section className="flex min-h-[100dvh] px-5 py-6 sm:px-8 sm:py-10 lg:px-12">
        <div
          className={
            step === "enroll"
              ? "mx-auto my-auto w-full max-w-lg py-2"
              : "mx-auto my-auto w-full max-w-[27rem] py-2"
          }
        >
          <div className="inline-flex">
            <BrandMark showName className="[&>span:last-child]:text-xl" />
          </div>

          <header className="mb-7 mt-10 sm:mt-12">
            <h1 className="font-display text-2xl font-semibold text-foreground sm:text-3xl">
              {stepTitle[step]}
            </h1>
            {step === "email" ? (
              <p className="mt-2 text-sm leading-6 text-foreground-secondary">
                Введите рабочий email, чтобы получить одноразовый код.
              </p>
            ) : step === "code" ? (
              <p className="mt-2 break-words text-sm leading-6 text-foreground-secondary">
                Код отправлен на <span className="font-medium text-foreground">{email}</span>
              </p>
            ) : step === "mfa" ? (
              <p className="mt-2 text-sm leading-6 text-foreground-secondary">
                Введите код из приложения-аутентификатора.
              </p>
            ) : step === "enroll" ? (
              <p className="mt-2 text-sm leading-6 text-foreground-secondary">
                Добавьте Aurum Pharma в приложение-аутентификатор.
              </p>
            ) : (
              <p className="mt-2 text-sm leading-6 text-foreground-secondary">
                Используйте один из сохранённых одноразовых кодов.
              </p>
            )}
          </header>

          {step === "email" ? (
            <form onSubmit={submitEmail} noValidate className="space-y-5">
              <div>
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  autoFocus
                  placeholder="name@pharmacy.tj"
                  invalid={Boolean(emailForm.formState.errors.email)}
                  aria-describedby={emailForm.formState.errors.email ? "email-error" : undefined}
                  {...emailForm.register("email")}
                />
                <FormError id="email-error">{emailForm.formState.errors.email?.message}</FormError>
              </div>
              <LoginError message={topError} />
              <Button
                type="submit"
                size="lg"
                className="w-full"
                isLoading={emailForm.formState.isSubmitting}
              >
                Получить код
              </Button>
            </form>
          ) : step === "code" ? (
            <form onSubmit={submitCode} noValidate className="space-y-5">
              {devCode ? (
                <div className="rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
                  <span className="font-medium">Dev-режим:</span> код{" "}
                  <code className="rounded bg-warning/15 px-1.5 py-0.5 font-mono text-base font-semibold">
                    {devCode}
                  </code>
                  {" — "}уже подставлен.
                </div>
              ) : null}
              <div>
                <Label htmlFor="code">Код из письма</Label>
                <Input
                  id="code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  autoFocus
                  className="font-mono text-base"
                  invalid={Boolean(codeForm.formState.errors.code)}
                  aria-describedby={codeForm.formState.errors.code ? "code-error" : undefined}
                  {...codeForm.register("code")}
                />
                <FormError id="code-error">{codeForm.formState.errors.code?.message}</FormError>
              </div>
              <div>
                <Label htmlFor="password">Пароль (если задан)</Label>
                <div className="relative">
                  <Input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    className="pr-24"
                    {...codeForm.register("password")}
                  />
                  <button
                    type="button"
                    className="absolute inset-y-1 right-1 rounded-sm px-3 text-xs font-semibold text-foreground-secondary transition-colors duration-fast hover:bg-foreground/5 hover:text-foreground"
                    aria-pressed={showPassword}
                    onClick={() => setShowPassword((visible) => !visible)}
                  >
                    {showPassword ? "Скрыть" : "Показать"}
                  </button>
                </div>
                <FormError>{codeForm.formState.errors.password?.message}</FormError>
              </div>
              <LoginError message={topError} />
              <Button
                type="submit"
                size="lg"
                className="w-full"
                isLoading={codeForm.formState.isSubmitting}
              >
                Войти
              </Button>
            </form>
          ) : step === "mfa" ? (
            <form onSubmit={submitMfa} noValidate className="space-y-5">
              <div>
                <Label htmlFor="mfa-code">Код подтверждения</Label>
                <Input
                  id="mfa-code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  autoFocus
                  className="font-mono text-base"
                  invalid={Boolean(mfaForm.formState.errors.code)}
                  aria-describedby={mfaForm.formState.errors.code ? "mfa-error" : undefined}
                  {...mfaForm.register("code")}
                />
                <FormError id="mfa-error">{mfaForm.formState.errors.code?.message}</FormError>
              </div>
              <LoginError message={topError} />
              <Button
                type="submit"
                size="lg"
                className="w-full"
                isLoading={mfaForm.formState.isSubmitting}
              >
                Подтвердить
              </Button>
              <Button
                type="button"
                variant="secondary"
                className="w-full"
                disabled={authRequestBusy}
                onClick={() => {
                  if (activeRequestRef.current) return;
                  setTopError(null);
                  setStep("recovery");
                }}
              >
                Использовать резервный код
              </Button>
            </form>
          ) : step === "enroll" ? (
            enrollment ? (
              <form onSubmit={submitEnrollment} noValidate className="space-y-5">
                <div>
                  <Label>Секретный ключ</Label>
                  <code className="mt-1 block select-all break-all rounded-md border border-border bg-foreground/[0.03] px-3 py-2 font-mono text-sm text-foreground">
                    {enrollment.secret}
                  </code>
                </div>
                <div>
                  <Label>Резервные коды</Label>
                  <div
                    className="mt-1 grid grid-cols-1 gap-2 rounded-md border border-border bg-foreground/[0.03] p-3 sm:grid-cols-2"
                    role="group"
                    aria-label="Резервные коды"
                  >
                    {enrollment.recovery_codes.map((code) => (
                      <code key={code} className="select-all break-all font-mono text-xs">
                        {code}
                      </code>
                    ))}
                  </div>
                </div>
                <label className="flex items-start gap-2 text-sm text-foreground-secondary">
                  <Checkbox
                    className="mt-0.5"
                    aria-describedby={
                      enrollmentForm.formState.errors.saved ? "recovery-codes-error" : undefined
                    }
                    {...enrollmentForm.register("saved")}
                  />
                  <span>Я сохранил резервные коды в безопасном месте</span>
                </label>
                <FormError id="recovery-codes-error">
                  {enrollmentForm.formState.errors.saved?.message}
                </FormError>
                <div>
                  <Label htmlFor="enrollment-code">Код из приложения</Label>
                  <Input
                    id="enrollment-code"
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    className="font-mono text-base"
                    invalid={Boolean(enrollmentForm.formState.errors.code)}
                    aria-describedby={
                      enrollmentForm.formState.errors.code ? "enrollment-code-error" : undefined
                    }
                    {...enrollmentForm.register("code")}
                  />
                  <FormError id="enrollment-code-error">
                    {enrollmentForm.formState.errors.code?.message}
                  </FormError>
                </div>
                <LoginError message={topError} />
                <Button
                  type="submit"
                  size="lg"
                  className="w-full"
                  isLoading={enrollmentForm.formState.isSubmitting}
                >
                  Включить двухфакторную защиту
                </Button>
              </form>
            ) : (
              <div className="space-y-5">
                <p className="text-sm text-foreground-secondary">
                  Подготавливаем двухфакторную защиту.
                </p>
                <LoginError message={topError} />
                <Button
                  type="button"
                  size="lg"
                  className="w-full"
                  isLoading={enrollmentLoading}
                  disabled={!challengeToken}
                  onClick={() => {
                    if (!challengeToken || activeRequestRef.current) return;
                    const controller = startRequest();
                    setTopError(null);
                    void beginEnrollment(challengeToken, controller)
                      .catch((err: unknown) => {
                        if (!controller.signal.aborted) setTopError(extractErrorMessage(err));
                      })
                      .finally(() => finishRequest(controller));
                  }}
                >
                  Повторить
                </Button>
              </div>
            )
          ) : (
            <form onSubmit={submitRecovery} noValidate className="space-y-5">
              <div>
                <Label htmlFor="recovery-code">Резервный код</Label>
                <Input
                  id="recovery-code"
                  type="text"
                  autoComplete="off"
                  autoCapitalize="characters"
                  autoFocus
                  className="font-mono"
                  invalid={Boolean(recoveryForm.formState.errors.recoveryCode)}
                  aria-describedby={
                    recoveryForm.formState.errors.recoveryCode ? "recovery-code-error" : undefined
                  }
                  {...recoveryForm.register("recoveryCode")}
                />
                <FormError id="recovery-code-error">
                  {recoveryForm.formState.errors.recoveryCode?.message}
                </FormError>
              </div>
              <LoginError message={topError} />
              <Button
                type="submit"
                size="lg"
                className="w-full"
                isLoading={recoveryForm.formState.isSubmitting}
              >
                Восстановить доступ
              </Button>
            </form>
          )}

          {step !== "email" ? (
            <div className="mt-6 border-t border-border pt-4">
              <Button variant="ghost" size="sm" disabled={authRequestBusy} onClick={resetToEmail}>
                Изменить email
              </Button>
            </div>
          ) : null}
        </div>
      </section>
    </main>
  );
}
