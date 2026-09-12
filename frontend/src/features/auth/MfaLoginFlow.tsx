import { type FormEvent, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { completeMfaEnrollment, recoverMfa, startMfaEnrollment, verifyMfa } from "./api";
import { type MfaChallengeResponse, type MfaEnrollmentSetup, type TokenPair } from "./types";

import { MfaEnrollmentForm } from "./MfaEnrollmentForm";

const mfaSchema = z.object({
  code: z.string().regex(/^\d{6}$/, "Код состоит из 6 цифр"),
});

const enrollmentSchema = mfaSchema.extend({
  saved: z.boolean().refine((value) => value, "Сохраните резервные коды"),
});

const recoverySchema = z.object({
  recoveryCode: z.string().min(20, "Введите резервный код").max(32, "Некорректный резервный код"),
});

type MfaForm = z.infer<typeof mfaSchema>;
type EnrollmentForm = z.infer<typeof enrollmentSchema>;
type RecoveryForm = z.infer<typeof recoverySchema>;
type LoginStep = "mfa" | "enroll" | "recovery";

const stepTitle: Record<LoginStep, string> = {
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

export function MfaLoginFlow({
  challenge,
  onAuthenticated,
  onBack,
  onEnrollment,
}: {
  challenge: MfaChallengeResponse;
  onAuthenticated: (tokens: TokenPair) => Promise<void>;
  onBack: () => void;
  onEnrollment: () => void;
}): JSX.Element {
  const [step, setStep] = useState<LoginStep>(() =>
    challenge.status === "mfa_enrollment_required"
      ? "enroll"
      : challenge.status === "mfa_recovery_required"
        ? "recovery"
        : "mfa",
  );
  const [topError, setTopError] = useState<string | null>(null);
  const [challengeToken, setChallengeToken] = useState(challenge.challenge_token);
  const [enrollment, setEnrollment] = useState<MfaEnrollmentSetup | null>(null);
  const [enrollmentLoading, setEnrollmentLoading] = useState(false);
  const activeRequestRef = useRef<AbortController | null>(null);
  const authFlowVersionRef = useRef(0);
  const mfaForm = useForm<MfaForm>({ defaultValues: { code: "" } });
  const enrollmentForm = useForm<EnrollmentForm>({
    defaultValues: { code: "", saved: false },
  });
  const recoveryForm = useForm<RecoveryForm>({
    defaultValues: { recoveryCode: "" },
  });

  useEffect(() => {
    if (challenge.status !== "mfa_enrollment_required") return;
    const controller = new AbortController();
    activeRequestRef.current = controller;
    // Defer the first request until StrictMode has finished its setup/cleanup
    // replay; an aborted replay must not rotate the pending enrollment secret.
    void Promise.resolve()
      .then(() => {
        if (controller.signal.aborted) return undefined;
        setEnrollmentLoading(true);
        return startMfaEnrollment(
          { challenge_token: challenge.challenge_token },
          controller.signal,
        );
      })
      .then((setup) => {
        if (setup && activeRequestRef.current === controller && !controller.signal.aborted)
          setEnrollment(setup);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setTopError(extractErrorMessage(error));
      })
      .finally(() => {
        if (activeRequestRef.current === controller) {
          activeRequestRef.current = null;
          if (!controller.signal.aborted) setEnrollmentLoading(false);
        }
      });
    return () => controller.abort();
  }, [challenge.status, challenge.challenge_token]);

  useEffect(
    () => () => {
      authFlowVersionRef.current += 1;
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
    onBack();
  };

  const beginEnrollment = async (token: string, controller: AbortController) => {
    onEnrollment();
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
      await onAuthenticated(tokens);
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
      setEnrollment(null);
      await onAuthenticated(tokens);
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
    mfaForm.formState.isSubmitting ||
    enrollmentForm.formState.isSubmitting ||
    recoveryForm.formState.isSubmitting ||
    enrollmentLoading;

  return (
    <>
      <header className="mb-7">
        <h1 className="font-display text-[29px] font-semibold leading-tight tracking-tight text-foreground sm:text-[34px]">
          {stepTitle[step]}
        </h1>
        <p className="mt-2 text-sm leading-6 text-foreground-secondary">
          {step === "mfa"
            ? "Введите код из приложения-аутентификатора."
            : step === "enroll"
              ? "Добавьте Aurum Pharma в приложение-аутентификатор."
              : "Используйте один из сохранённых одноразовых кодов."}
        </p>
      </header>
      {step === "mfa" ? (
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
          <MfaEnrollmentForm
            enrollment={enrollment}
            form={enrollmentForm}
            onSubmit={submitEnrollment}
            error={<LoginError message={topError} />}
          />
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

      <div className="mt-6 border-t border-border pt-4">
        <Button variant="ghost" size="sm" disabled={authRequestBusy} onClick={resetToEmail}>
          Вернуться ко входу
        </Button>
      </div>
    </>
  );
}
