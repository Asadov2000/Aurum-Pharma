import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { z } from "zod";

import {
  Button,
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
  FormError,
  Input,
  Label,
} from "@/components/ui";
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

function extractErrorMessage(err: unknown): string {
  return describeApiError(err, "Не удалось выполнить запрос. Проверьте соединение.");
}

export function LoginPage(): JSX.Element {
  const [step, setStep] = useState<LoginStep>("email");
  const [email, setEmail] = useState("");
  const [devCode, setDevCode] = useState<string | null>(null);
  const [topError, setTopError] = useState<string | null>(null);
  const [challengeToken, setChallengeToken] = useState<string | null>(null);
  const [enrollment, setEnrollment] = useState<MfaEnrollmentSetup | null>(null);
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

  const navigateAfterLogin = () => {
    const target = typeof search.from === "string" && search.from !== "/login" ? search.from : "/";
    navigate({ to: target });
  };

  const beginEnrollment = async (token: string) => {
    setStep("enroll");
    setEnrollment(null);
    const setup = await startMfaEnrollment({ challenge_token: token });
    setEnrollment(setup);
    enrollmentForm.reset({ code: "", saved: false });
  };

  const submitEmail = emailForm.handleSubmit(async (values) => {
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
    try {
      const res = await requestLoginCode({ email: parsed.data.email });
      setEmail(parsed.data.email);
      if (res.dev_code) {
        setDevCode(res.dev_code);
        codeForm.setValue("code", res.dev_code);
      } else {
        setDevCode(null);
      }
      setStep("code");
    } catch (err) {
      setTopError(extractErrorMessage(err));
    }
  });

  const submitCode = codeForm.handleSubmit(async (values) => {
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
    try {
      const result = await login({
        email,
        code: parsed.data.code,
        password: parsed.data.password ? parsed.data.password : undefined,
      });
      if ("access_token" in result) {
        navigateAfterLogin();
        return;
      }
      setChallengeToken(result.challenge_token);
      if (result.status === "mfa_enrollment_required") {
        await beginEnrollment(result.challenge_token);
      } else if (result.status === "mfa_recovery_required") {
        setStep("recovery");
      } else {
        setStep("mfa");
      }
    } catch (err) {
      setTopError(extractErrorMessage(err));
    }
  });

  const submitMfa = mfaForm.handleSubmit(async (values) => {
    if (!challengeToken) return;
    const parsed = mfaSchema.safeParse(values);
    if (!parsed.success) {
      mfaForm.setError("code", {
        message: parsed.error.issues[0]?.message ?? "Проверьте код",
      });
      return;
    }
    setTopError(null);
    try {
      const tokens = await verifyMfa({
        challenge_token: challengeToken,
        code: parsed.data.code,
      });
      setTokens(tokens);
      navigateAfterLogin();
    } catch (err) {
      setTopError(extractErrorMessage(err));
    }
  });

  const submitEnrollment = enrollmentForm.handleSubmit(async (values) => {
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
    try {
      const tokens = await completeMfaEnrollment({
        challenge_token: challengeToken,
        code: parsed.data.code,
      });
      setTokens(tokens);
      setEnrollment(null);
      navigateAfterLogin();
    } catch (err) {
      setTopError(extractErrorMessage(err));
    }
  });

  const submitRecovery = recoveryForm.handleSubmit(async (values) => {
    if (!challengeToken) return;
    const parsed = recoverySchema.safeParse(values);
    if (!parsed.success) {
      recoveryForm.setError("recoveryCode", {
        message: parsed.error.issues[0]?.message ?? "Проверьте резервный код",
      });
      return;
    }
    setTopError(null);
    try {
      const result = await recoverMfa({
        challenge_token: challengeToken,
        recovery_code: parsed.data.recoveryCode,
      });
      await beginEnrollment(result.challenge_token);
    } catch (err) {
      setTopError(extractErrorMessage(err));
    }
  });

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className={step === "enroll" ? "w-full max-w-lg" : "w-full max-w-sm"}>
        <CardHeader>
          <CardTitle>Вход в Aurum Pharma</CardTitle>
        </CardHeader>
        <CardContent>
          {step === "email" ? (
            <form onSubmit={submitEmail} noValidate className="space-y-4">
              <div>
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  autoFocus
                  invalid={Boolean(emailForm.formState.errors.email)}
                  {...emailForm.register("email")}
                />
                <FormError>{emailForm.formState.errors.email?.message}</FormError>
              </div>
              {topError && <p className="text-sm text-danger">{topError}</p>}
              <Button type="submit" className="w-full" isLoading={emailForm.formState.isSubmitting}>
                Получить код
              </Button>
            </form>
          ) : step === "code" ? (
            <form onSubmit={submitCode} noValidate className="space-y-4">
              <p className="text-sm text-foreground-secondary">
                Код отправлен на <span className="font-medium text-foreground">{email}</span>
              </p>
              {devCode && (
                <div className="rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
                  <span className="font-medium">Dev-режим:</span> код{" "}
                  <code className="rounded bg-warning/15 px-1.5 py-0.5 font-mono text-base font-semibold">
                    {devCode}
                  </code>
                  {" — "}уже подставлен в поле ниже.
                </div>
              )}
              <div>
                <Label htmlFor="code">Код из письма</Label>
                <Input
                  id="code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  autoFocus
                  invalid={Boolean(codeForm.formState.errors.code)}
                  {...codeForm.register("code")}
                />
                <FormError>{codeForm.formState.errors.code?.message}</FormError>
              </div>
              <div>
                <Label htmlFor="password">Пароль (если задан)</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  {...codeForm.register("password")}
                />
                <FormError>{codeForm.formState.errors.password?.message}</FormError>
              </div>
              {topError && <p className="text-sm text-danger">{topError}</p>}
              <Button type="submit" className="w-full" isLoading={codeForm.formState.isSubmitting}>
                Войти
              </Button>
            </form>
          ) : step === "mfa" ? (
            <form onSubmit={submitMfa} noValidate className="space-y-4">
              <p className="text-sm text-foreground-secondary">
                Введите код из приложения-аутентификатора.
              </p>
              <div>
                <Label htmlFor="mfa-code">Код подтверждения</Label>
                <Input
                  id="mfa-code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  autoFocus
                  invalid={Boolean(mfaForm.formState.errors.code)}
                  {...mfaForm.register("code")}
                />
                <FormError>{mfaForm.formState.errors.code?.message}</FormError>
              </div>
              {topError && <p className="text-sm text-danger">{topError}</p>}
              <Button type="submit" className="w-full" isLoading={mfaForm.formState.isSubmitting}>
                Подтвердить
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="w-full"
                onClick={() => {
                  setTopError(null);
                  setStep("recovery");
                }}
              >
                Использовать резервный код
              </Button>
            </form>
          ) : step === "enroll" ? (
            enrollment ? (
              <form onSubmit={submitEnrollment} noValidate className="space-y-4">
                <p className="text-sm text-foreground-secondary">
                  Добавьте Aurum Pharma в приложение-аутентификатор вручную.
                </p>
                <div>
                  <Label>Секретный ключ</Label>
                  <code className="mt-1 block break-all rounded-md border bg-background-subtle px-3 py-2 font-mono text-sm">
                    {enrollment.secret}
                  </code>
                </div>
                <div>
                  <Label>Резервные коды</Label>
                  <div className="mt-1 grid grid-cols-2 gap-2 rounded-md border bg-background-subtle p-3">
                    {enrollment.recovery_codes.map((code) => (
                      <code key={code} className="font-mono text-xs">
                        {code}
                      </code>
                    ))}
                  </div>
                </div>
                <label className="flex items-start gap-2 text-sm text-foreground-secondary">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4"
                    {...enrollmentForm.register("saved")}
                  />
                  <span>Я сохранил резервные коды в безопасном месте</span>
                </label>
                <FormError>{enrollmentForm.formState.errors.saved?.message}</FormError>
                <div>
                  <Label htmlFor="enrollment-code">Код из приложения</Label>
                  <Input
                    id="enrollment-code"
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    invalid={Boolean(enrollmentForm.formState.errors.code)}
                    {...enrollmentForm.register("code")}
                  />
                  <FormError>{enrollmentForm.formState.errors.code?.message}</FormError>
                </div>
                {topError && <p className="text-sm text-danger">{topError}</p>}
                <Button
                  type="submit"
                  className="w-full"
                  isLoading={enrollmentForm.formState.isSubmitting}
                >
                  Включить двухфакторную защиту
                </Button>
              </form>
            ) : (
              <div className="space-y-4">
                <p className="text-sm text-foreground-secondary">
                  Подготавливаем двухфакторную защиту.
                </p>
                {topError && <p className="text-sm text-danger">{topError}</p>}
                <Button
                  type="button"
                  className="w-full"
                  onClick={() => {
                    if (!challengeToken) return;
                    setTopError(null);
                    void beginEnrollment(challengeToken).catch((err: unknown) => {
                      setTopError(extractErrorMessage(err));
                    });
                  }}
                >
                  Повторить
                </Button>
              </div>
            )
          ) : (
            <form onSubmit={submitRecovery} noValidate className="space-y-4">
              <p className="text-sm text-foreground-secondary">
                Используйте один из сохранённых одноразовых резервных кодов.
              </p>
              <div>
                <Label htmlFor="recovery-code">Резервный код</Label>
                <Input
                  id="recovery-code"
                  type="text"
                  autoComplete="off"
                  autoCapitalize="characters"
                  autoFocus
                  invalid={Boolean(recoveryForm.formState.errors.recoveryCode)}
                  {...recoveryForm.register("recoveryCode")}
                />
                <FormError>{recoveryForm.formState.errors.recoveryCode?.message}</FormError>
              </div>
              {topError && <p className="text-sm text-danger">{topError}</p>}
              <Button
                type="submit"
                className="w-full"
                isLoading={recoveryForm.formState.isSubmitting}
              >
                Восстановить доступ
              </Button>
            </form>
          )}
        </CardContent>
        {step !== "email" && step !== "enroll" && (
          <CardFooter>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setTopError(null);
                setChallengeToken(null);
                setEnrollment(null);
                mfaForm.reset();
                recoveryForm.reset();
                setStep("email");
              }}
            >
              Изменить email
            </Button>
          </CardFooter>
        )}
      </Card>
    </div>
  );
}
