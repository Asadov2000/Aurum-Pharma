import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { isAxiosError } from "axios";
import { z } from "zod";
import { Button, Checkbox, FormError, Input, Label } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";
import { requestPasswordResetCode, resetAccountPassword, type ContactChannel } from "./identityApi";
import {
  emailIdentitySchema,
  newPasswordSchema,
  oneTimeCodeSchema,
  phoneIdentitySchema,
} from "./identityForms";
import { LoginMethodSwitch } from "./LoginMethodSwitch";
import { useLoginMethodsQuery } from "./identityQueries";
import { codeRetryAfter } from "./accountSecurityErrors";

const passwordSchema = z
  .object({
    code: oneTimeCodeSchema,
    new_password: newPasswordSchema,
    confirmation: z.string(),
    mfa_enabled: z.boolean(),
    recovery: z.boolean(),
    factor: z.string(),
  })
  .superRefine((value, context) => {
    if (value.new_password !== value.confirmation)
      context.addIssue({ code: "custom", path: ["confirmation"], message: "Пароли не совпадают" });
    if (value.mfa_enabled && !(value.recovery ? /^.{20,32}$/ : /^\d{6}$/).test(value.factor.trim()))
      context.addIssue({
        code: "custom",
        path: ["factor"],
        message: value.recovery ? "Введите резервный код" : "Введите 6 цифр из приложения",
      });
  });

type Fields = z.infer<typeof passwordSchema> & { contact: string };

export function PasswordResetForm({
  initialChannel,
  onBack,
}: {
  initialChannel: ContactChannel;
  onBack: () => void;
}): JSX.Element {
  const [channel, setChannel] = useState<ContactChannel>(initialChannel);
  const [contact, setContact] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retrySeconds, setRetrySeconds] = useState(0);
  const [resending, setResending] = useState(false);
  const activeRequest = useRef<AbortController | null>(null);
  const form = useForm<Fields>({
    defaultValues: {
      contact: initialChannel === "phone" ? "+992" : "",
      code: "",
      new_password: "",
      confirmation: "",
      mfa_enabled: false,
      recovery: false,
      factor: "",
    },
  });
  const busy = form.formState.isSubmitting || resending;
  const methods = useLoginMethodsQuery();
  const phoneAvailable = methods.data?.phone_login_enabled === true;
  const mfaEnabled = form.watch("mfa_enabled");
  const recovery = form.watch("recovery");
  useEffect(() => () => activeRequest.current?.abort(), []);
  useEffect(() => {
    if (retrySeconds <= 0) return;
    const timer = window.setTimeout(
      () => setRetrySeconds((value) => Math.max(0, value - 1)),
      1_000,
    );
    return () => window.clearTimeout(timer);
  }, [retrySeconds]);

  const submit = form.handleSubmit(async (values) => {
    if (activeRequest.current) return;
    if (channel === "phone" && !phoneAvailable) return;
    form.clearErrors();
    setError(null);
    const identity = (channel === "phone" ? phoneIdentitySchema : emailIdentitySchema).safeParse(
      values.contact,
    );
    if (!identity.success) {
      form.setError("contact", { message: identity.error.issues[0]?.message });
      return;
    }
    if (contact) {
      const parsed = passwordSchema.safeParse(values);
      if (!parsed.success) {
        for (const issue of parsed.error.issues) {
          const field = issue.path[0];
          if (
            field === "code" ||
            field === "new_password" ||
            field === "confirmation" ||
            field === "factor"
          )
            form.setError(field, { message: issue.message });
        }
        return;
      }
    } else if (retrySeconds > 0) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    try {
      if (!contact) {
        const result = await requestPasswordResetCode(
          { channel, contact: identity.data },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setContact(identity.data);
        setRetrySeconds(60);
        form.setValue("code", result.dev_code ?? "");
      } else {
        await resetAccountPassword(
          {
            channel,
            contact,
            code: values.code.trim(),
            new_password: values.new_password,
            mfa_code: values.mfa_enabled && !values.recovery ? values.factor.trim() : undefined,
            recovery_code: values.mfa_enabled && values.recovery ? values.factor.trim() : undefined,
          },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        form.reset();
        setDone(true);
      }
    } catch (err) {
      if (!controller.signal.aborted) {
        if (
          isAxiosError<{ error?: { details?: { reason?: string } } }>(err) &&
          err.response?.data.error?.details?.reason === "mfa_required"
        ) {
          form.setValue("mfa_enabled", true);
          setError("Подтвердите двухфакторную защиту кодом из приложения или резервным кодом.");
        } else if (isAxiosError(err) && err.response?.status === 401) {
          setError(
            "Не удалось подтвердить восстановление. Проверьте код и второй фактор, если защита включена.",
          );
        } else
          setError(
            describeApiError(
              err,
              "Не удалось восстановить пароль. Проверьте код и подтверждение двухфакторной защиты, если она включена.",
            ),
          );
        const retryAfter = codeRetryAfter(err);
        if (retryAfter) setRetrySeconds(retryAfter);
      }
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
    }
  });

  const resendCode = async () => {
    if (
      !contact ||
      activeRequest.current ||
      retrySeconds > 0 ||
      (channel === "phone" && !phoneAvailable)
    )
      return;
    const controller = new AbortController();
    activeRequest.current = controller;
    setResending(true);
    setError(null);
    try {
      const result = await requestPasswordResetCode({ channel, contact }, controller.signal);
      if (controller.signal.aborted) return;
      form.setValue("code", result.dev_code ?? "");
      setRetrySeconds(60);
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(describeApiError(err, "Не удалось отправить новый код восстановления."));
        const retryAfter = codeRetryAfter(err);
        if (retryAfter) setRetrySeconds(retryAfter);
      }
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
      if (!controller.signal.aborted) setResending(false);
    }
  };

  if (done)
    return (
      <div className="space-y-5">
        <h1 className="font-display text-3xl font-semibold">Пароль обновлён</h1>
        <p className="text-sm leading-6 text-foreground-secondary">
          Войдите с новым паролем. Другие сеансы завершены, настройки двухфакторной защиты
          сохранены.
        </p>
        <Button type="button" className="w-full" onClick={onBack}>
          Вернуться ко входу
        </Button>
      </div>
    );

  return (
    <>
      <header className="mb-7">
        <h1 className="font-display text-[29px] font-semibold leading-tight sm:text-[34px]">
          {contact ? "Новый пароль" : "Забыли пароль?"}
        </h1>
        <p className="mt-3 text-sm leading-6 text-foreground-secondary">
          {contact
            ? "Если аккаунт с такими данными существует, код восстановления придёт на указанный контакт. Введите код и новый пароль."
            : "Укажите почту или подтверждённый телефон вашего аккаунта."}
        </p>
      </header>
      {!contact && (
        <LoginMethodSwitch
          value={channel}
          disabled={busy}
          label="Способ восстановления"
          onChange={(value) => {
            setChannel(value);
            form.setValue("contact", value === "phone" ? "+992" : "");
            form.clearErrors();
            setError(null);
          }}
        />
      )}
      {channel === "phone" && !phoneAvailable && (
        <p role="status" className="mb-4 text-sm text-foreground-secondary">
          SMS сейчас недоступны. Используйте подтверждённую почту.
        </p>
      )}
      <form onSubmit={submit} noValidate className="space-y-4">
        {!contact ? (
          <div>
            <Label htmlFor="reset-contact">
              {channel === "email" ? "Email" : "Номер телефона"}
            </Label>
            <Input
              id="reset-contact"
              type={channel === "email" ? "email" : "tel"}
              autoComplete={channel === "email" ? "email" : "tel"}
              autoFocus
              invalid={Boolean(form.formState.errors.contact)}
              {...form.register("contact")}
              aria-describedby={form.formState.errors.contact ? "reset-contact-error" : undefined}
            />
            <FormError id="reset-contact-error">{form.formState.errors.contact?.message}</FormError>
          </div>
        ) : (
          <>
            <div>
              <Label htmlFor="reset-code">Код восстановления</Label>
              <Input
                id="reset-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                autoFocus
                {...form.register("code")}
                invalid={Boolean(form.formState.errors.code)}
                aria-describedby={form.formState.errors.code ? "reset-code-error" : undefined}
              />
              <FormError id="reset-code-error">{form.formState.errors.code?.message}</FormError>
            </div>
            <div>
              <Label htmlFor="reset-password">Новый пароль</Label>
              <Input
                id="reset-password"
                type="password"
                autoComplete="new-password"
                maxLength={128}
                {...form.register("new_password")}
                invalid={Boolean(form.formState.errors.new_password)}
                aria-describedby={
                  form.formState.errors.new_password ? "reset-password-error" : undefined
                }
              />
              <FormError id="reset-password-error">
                {form.formState.errors.new_password?.message}
              </FormError>
              <p className="mt-1 text-xs text-foreground-muted">
                От 12 символов. Можно использовать длинную фразу.
              </p>
            </div>
            <div>
              <Label htmlFor="reset-confirmation">Повторите пароль</Label>
              <Input
                id="reset-confirmation"
                type="password"
                autoComplete="new-password"
                maxLength={128}
                {...form.register("confirmation")}
                invalid={Boolean(form.formState.errors.confirmation)}
                aria-describedby={
                  form.formState.errors.confirmation ? "reset-confirmation-error" : undefined
                }
              />
              <FormError id="reset-confirmation-error">
                {form.formState.errors.confirmation?.message}
              </FormError>
            </div>
            <label className="flex min-h-11 items-center gap-2 text-sm">
              <Checkbox {...form.register("mfa_enabled")} />У меня включена двухфакторная защита
            </label>
            {mfaEnabled && (
              <div className="space-y-3">
                <label className="flex min-h-11 items-center gap-2 text-sm">
                  <Checkbox {...form.register("recovery")} />
                  Использовать резервный код
                </label>
                <Label htmlFor="reset-factor">
                  {recovery ? "Резервный код" : "Код из приложения"}
                </Label>
                <Input
                  id="reset-factor"
                  autoComplete="off"
                  maxLength={recovery ? 32 : 6}
                  inputMode={recovery ? "text" : "numeric"}
                  {...form.register("factor")}
                  invalid={Boolean(form.formState.errors.factor)}
                  aria-describedby={form.formState.errors.factor ? "reset-factor-error" : undefined}
                />
                <FormError id="reset-factor-error">
                  {form.formState.errors.factor?.message}
                </FormError>
              </div>
            )}
            <p className="text-xs leading-5 text-foreground-secondary">
              Если двухфакторная защита включена, потребуется её подтверждение. Сброс пароля не
              отключает защиту.
            </p>
          </>
        )}
        {error && (
          <p
            role="alert"
            className="rounded-lg bg-danger-subtle p-3 text-sm text-danger-foreground"
          >
            {error}
          </p>
        )}
        <Button
          type="submit"
          className="w-full"
          isLoading={busy}
          disabled={(channel === "phone" && !phoneAvailable) || (!contact && retrySeconds > 0)}
        >
          {contact
            ? "Сохранить пароль"
            : retrySeconds > 0
              ? `Повторить через ${retrySeconds} с`
              : "Получить код восстановления"}
        </Button>
      </form>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button type="button" variant="ghost" disabled={busy} onClick={onBack}>
          Вернуться ко входу
        </Button>
        {contact && (
          <Button
            type="button"
            variant="ghost"
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
