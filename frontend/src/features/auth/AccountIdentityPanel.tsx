import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { Link } from "@tanstack/react-router";
import { NavIcon } from "@/components/layout/icons";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  FormError,
  Input,
  Label,
} from "@/components/ui";
import { describeAccountSecurityError } from "./accountSecurityErrors";
import { useMfaSettingsQuery, mfaSettingsQueryKey } from "./accountSecurityQueries";
import {
  requestPhoneChangeCode,
  requestPhoneReauthenticationCode,
  verifyPhoneChangeCode,
} from "./identityApi";
import {
  accountIdentityQueryKey,
  useAccountIdentityQuery,
  useLoginMethodsQuery,
} from "./identityQueries";
import { oneTimeCodeSchema, phoneIdentitySchema } from "./identityForms";
import { PasswordSetupForm } from "./PasswordSetupForm";

const phoneChangeSchema = z.object({
  phone: phoneIdentitySchema,
  password: z.string(),
  reauth_code: z.string(),
  mfa_code: z.string(),
  code: z.string(),
});
type Fields = z.infer<typeof phoneChangeSchema>;

export function AccountIdentityPanel({
  presentation = "default",
}: { presentation?: "default" | "security" } = {}): JSX.Element {
  const security = presentation === "security";
  const identity = useAccountIdentityQuery();
  const methods = useLoginMethodsQuery();
  const mfa = useMfaSettingsQuery();
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [passwordSetup, setPasswordSetup] = useState(false);
  const [pendingPhone, setPendingPhone] = useState<string | null>(null);
  const [reauthSent, setReauthSent] = useState(false);
  const [reauthSending, setReauthSending] = useState(false);
  const [retrySeconds, setRetrySeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const requestBusy = useRef(false);
  const form = useForm<Fields>({
    defaultValues: { phone: "+992", password: "", reauth_code: "", mfa_code: "", code: "" },
  });
  const smsAvailable = methods.data?.phone_login_enabled === true;
  const busy = form.formState.isSubmitting || reauthSending;
  useEffect(() => {
    if (retrySeconds <= 0) return;
    const timer = window.setTimeout(
      () => setRetrySeconds((value) => Math.max(0, value - 1)),
      1_000,
    );
    return () => window.clearTimeout(timer);
  }, [retrySeconds]);

  const submit = form.handleSubmit(async (values) => {
    if (requestBusy.current || !identity.data || !mfa.data || !smsAvailable) return;
    form.clearErrors();
    setError(null);
    const parsed = pendingPhone
      ? oneTimeCodeSchema.safeParse(values.code)
      : phoneChangeSchema.safeParse(values);
    if (!parsed.success) {
      if (pendingPhone) form.setError("code", { message: parsed.error.issues[0]?.message });
      else
        for (const issue of parsed.error.issues) {
          if (issue.path[0] === "phone") form.setError("phone", { message: issue.message });
        }
      return;
    }
    if (!pendingPhone) {
      if (identity.data.has_password && !values.password) {
        form.setError("password", { message: "Введите текущий пароль" });
        return;
      }
      if (!identity.data.has_password && !oneTimeCodeSchema.safeParse(values.reauth_code).success) {
        form.setError("reauth_code", { message: "Введите 6 цифр из письма" });
        return;
      }
      if (mfa.data.enabled && !oneTimeCodeSchema.safeParse(values.mfa_code).success) {
        form.setError("mfa_code", { message: "Введите 6 цифр из приложения" });
        return;
      }
    }
    requestBusy.current = true;
    try {
      if (pendingPhone) {
        await verifyPhoneChangeCode({ phone: pendingPhone, code: values.code.trim() });
        setEditing(false);
        setPendingPhone(null);
        setReauthSent(false);
        form.reset();
        setSuccess("Телефон подтверждён. Теперь можно входить по SMS.");
        await client.invalidateQueries({ queryKey: accountIdentityQueryKey });
      } else {
        const phone = phoneIdentitySchema.parse(values.phone);
        const result = await requestPhoneChangeCode({
          phone,
          password: identity.data.has_password ? values.password : undefined,
          reauth_code: !identity.data.has_password ? values.reauth_code.trim() : undefined,
          mfa_code: mfa.data.enabled ? values.mfa_code.trim() : undefined,
        });
        setPendingPhone(phone);
        form.setValue("password", "");
        form.setValue("reauth_code", "");
        form.setValue("mfa_code", "");
        form.setValue("code", result.dev_code ?? "");
      }
    } catch (err) {
      setError(
        describeAccountSecurityError(
          err,
          "Не удалось изменить телефон. Проверьте подтверждение и повторите.",
        ),
      );
    } finally {
      requestBusy.current = false;
    }
  });

  const sendReauth = async () => {
    if (requestBusy.current || retrySeconds > 0) return;
    requestBusy.current = true;
    setReauthSending(true);
    setError(null);
    try {
      const result = await requestPhoneReauthenticationCode();
      setReauthSent(true);
      setRetrySeconds(60);
      form.setValue("reauth_code", result.dev_code ?? "");
    } catch (err) {
      setError(describeAccountSecurityError(err, "Не удалось отправить подтверждение на почту."));
    } finally {
      requestBusy.current = false;
      setReauthSending(false);
    }
  };

  return (
    <Card className={security ? "min-w-0 rounded-xl" : undefined}>
      <CardHeader>
        <CardTitle className={security ? "flex items-center gap-2" : undefined}>
          {security ? (
            <span className="text-primary">
              <NavIcon to="/security" />
            </span>
          ) : null}
          Способы входа
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        {security ? (
          <p className="text-sm text-foreground-muted">
            Входите по почте и паролю или по подтверждённому телефону и коду из SMS.
          </p>
        ) : null}
        {identity.isPending ? (
          <p className="text-sm text-foreground-muted">Загрузка способов входа…</p>
        ) : identity.isError ? (
          <div className="space-y-3">
            <p role="alert" className="text-sm text-danger">
              Не удалось загрузить способы входа.
            </p>
            <Button variant="secondary" onClick={() => void identity.refetch()}>
              Повторить
            </Button>
          </div>
        ) : (
          identity.data && (
            <>
              <div
                className={`flex flex-wrap items-center justify-between gap-3 ${security ? "rounded-lg bg-foreground/[0.03] p-4" : ""}`}
              >
                <div className="min-w-0">
                  <p className="font-medium">Почта и пароль</p>
                  <p className="text-sm text-foreground-secondary">
                    {identity.data.has_password
                      ? "Пароль настроен. Если забыли его, воспользуйтесь восстановлением на странице входа."
                      : "Создайте пароль для входа по почте и подтверждения важных действий."}
                  </p>
                </div>
                {!identity.data.has_password && !passwordSetup && (
                  <Button type="button" variant="secondary" onClick={() => setPasswordSetup(true)}>
                    Создать пароль
                  </Button>
                )}
              </div>
              {passwordSetup && !identity.data.has_password && (
                <PasswordSetupForm
                  phoneAvailable={identity.data.phone_verified && smsAvailable}
                  onComplete={() => {
                    setPasswordSetup(false);
                    void client.invalidateQueries({ queryKey: accountIdentityQueryKey });
                    void client.invalidateQueries({ queryKey: mfaSettingsQueryKey });
                  }}
                />
              )}
              <div className="border-t border-border pt-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium">Телефон и SMS</p>
                    <p className="break-words text-sm text-foreground-secondary">
                      {identity.data.phone_verified
                        ? identity.data.phone
                        : "Подтверждённый номер не добавлен"}
                    </p>
                    {security && identity.data.phone_verified ? (
                      <Badge className="mt-2" tone="success">
                        Подтверждён
                      </Badge>
                    ) : null}
                  </div>
                  {!editing && (
                    <Button
                      type="button"
                      variant="secondary"
                      disabled={!smsAvailable || !mfa.data}
                      onClick={() => {
                        setEditing(true);
                        setError(null);
                        setSuccess(null);
                      }}
                    >
                      {identity.data.phone_verified ? "Изменить номер" : "Добавить номер"}
                    </Button>
                  )}
                </div>
                {!smsAvailable && (
                  <p className="mt-2 text-sm text-foreground-muted">
                    SMS пока недоступны. Вход по почте продолжает работать.
                  </p>
                )}
              </div>
              {editing && (
                <form onSubmit={submit} noValidate className="max-w-md space-y-4">
                  {pendingPhone ? (
                    <>
                      <p className="break-words text-sm text-foreground-secondary">
                        SMS отправлено на новый номер {pendingPhone}. Номер изменится после
                        подтверждения.
                      </p>
                      <div>
                        <Label htmlFor="new-phone-code">Код на новый номер</Label>
                        <Input
                          id="new-phone-code"
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          maxLength={6}
                          autoFocus
                          {...form.register("code")}
                          invalid={Boolean(form.formState.errors.code)}
                          aria-describedby={
                            form.formState.errors.code ? "new-phone-code-error" : undefined
                          }
                        />
                        <FormError id="new-phone-code-error">
                          {form.formState.errors.code?.message}
                        </FormError>
                      </div>
                    </>
                  ) : (
                    <>
                      <div>
                        <Label htmlFor="new-account-phone">Новый номер телефона</Label>
                        <Input
                          id="new-account-phone"
                          type="tel"
                          autoComplete="tel"
                          placeholder="+992 90 123 45 67"
                          autoFocus
                          {...form.register("phone")}
                          invalid={Boolean(form.formState.errors.phone)}
                          aria-describedby={
                            form.formState.errors.phone ? "new-account-phone-error" : undefined
                          }
                        />
                        <FormError id="new-account-phone-error">
                          {form.formState.errors.phone?.message}
                        </FormError>
                      </div>
                      <p className="text-sm text-foreground-secondary">
                        Сначала подтвердите, что это вы. SMS отправим только на новый номер.
                      </p>
                      {identity.data.has_password ? (
                        <div>
                          <Label htmlFor="phone-current-password">Текущий пароль</Label>
                          <Input
                            id="phone-current-password"
                            type="password"
                            autoComplete="current-password"
                            maxLength={128}
                            {...form.register("password")}
                            invalid={Boolean(form.formState.errors.password)}
                            aria-describedby={
                              form.formState.errors.password
                                ? "phone-current-password-error"
                                : undefined
                            }
                          />
                          <FormError id="phone-current-password-error">
                            {form.formState.errors.password?.message}
                          </FormError>
                        </div>
                      ) : (
                        <div className="space-y-3">
                          <Button
                            type="button"
                            variant="secondary"
                            disabled={busy || retrySeconds > 0}
                            isLoading={reauthSending}
                            onClick={() => void sendReauth()}
                          >
                            {retrySeconds > 0
                              ? `Повторить через ${retrySeconds} с`
                              : reauthSent
                                ? "Отправить код на почту повторно"
                                : "Подтвердить личность через почту"}
                          </Button>
                          <Label htmlFor="phone-reauth-code">Код из письма</Label>
                          <Input
                            id="phone-reauth-code"
                            inputMode="numeric"
                            autoComplete="one-time-code"
                            maxLength={6}
                            {...form.register("reauth_code")}
                            invalid={Boolean(form.formState.errors.reauth_code)}
                            aria-describedby={
                              form.formState.errors.reauth_code
                                ? "phone-reauth-code-error"
                                : undefined
                            }
                          />
                          <FormError id="phone-reauth-code-error">
                            {form.formState.errors.reauth_code?.message}
                          </FormError>
                        </div>
                      )}
                      {mfa.data?.enabled && (
                        <div>
                          <Label htmlFor="phone-mfa-code">Код из приложения</Label>
                          <Input
                            id="phone-mfa-code"
                            inputMode="numeric"
                            autoComplete="one-time-code"
                            maxLength={6}
                            {...form.register("mfa_code")}
                            invalid={Boolean(form.formState.errors.mfa_code)}
                            aria-describedby={
                              form.formState.errors.mfa_code ? "phone-mfa-code-error" : undefined
                            }
                          />
                          <FormError id="phone-mfa-code-error">
                            {form.formState.errors.mfa_code?.message}
                          </FormError>
                        </div>
                      )}
                    </>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="submit"
                      isLoading={form.formState.isSubmitting}
                      disabled={reauthSending}
                    >
                      {pendingPhone ? "Подтвердить номер" : "Отправить SMS на новый номер"}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => {
                        setEditing(false);
                        setPendingPhone(null);
                        setReauthSent(false);
                        setError(null);
                        form.reset();
                      }}
                    >
                      Отмена
                    </Button>
                  </div>
                </form>
              )}
            </>
          )
        )}
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        {success && (
          <p role="status" className="text-sm text-success-foreground">
            {success}
          </p>
        )}
        {security ? (
          <p className="border-t border-border pt-4 text-sm text-foreground-muted">
            Почта и личные сведения находятся в разделе{" "}
            <Link to="/profile" className="font-medium text-primary underline underline-offset-4">
              «Мой профиль»
            </Link>
            .
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
