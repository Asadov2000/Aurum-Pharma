import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { z } from "zod";
import { AxiosError } from "axios";

import { Button, Card, CardContent, CardFooter, CardHeader, CardTitle, FormError, Input, Label } from "@/components/ui";

import { requestLoginCode } from "./api";
import { useAuth } from "./hooks";

const emailSchema = z.object({
  email: z.string().min(1, "Введите email").email("Некорректный email"),
});

const codeSchema = z.object({
  code: z.string().regex(/^\d{6}$/, "Код состоит из 6 цифр"),
  password: z.string().optional(),
});

type EmailForm = z.infer<typeof emailSchema>;
type CodeForm = z.infer<typeof codeSchema>;

function extractErrorMessage(err: unknown): string {
  if (err instanceof AxiosError) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") return detail;
    if (err.response?.status === 429) return "Слишком много попыток. Подождите минуту.";
    if (err.response?.status && err.response.status >= 500) return "Сервер недоступен. Попробуйте позже.";
  }
  return "Не удалось выполнить запрос. Проверьте соединение.";
}

export function LoginPage(): JSX.Element {
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [topError, setTopError] = useState<string | null>(null);
  const { login } = useAuth();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { from?: string };

  const emailForm = useForm<EmailForm>({ defaultValues: { email: "" } });
  const codeForm = useForm<CodeForm>({ defaultValues: { code: "", password: "" } });

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
      await requestLoginCode({ email: parsed.data.email });
      setEmail(parsed.data.email);
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
      await login({
        email,
        code: parsed.data.code,
        password: parsed.data.password ? parsed.data.password : undefined,
      });
      const target = typeof search.from === "string" && search.from !== "/login" ? search.from : "/";
      navigate({ to: target });
    } catch (err) {
      setTopError(extractErrorMessage(err));
    }
  });

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <Card className="w-full max-w-sm">
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
              {topError && <p className="text-sm text-red-600">{topError}</p>}
              <Button type="submit" className="w-full" isLoading={emailForm.formState.isSubmitting}>
                Получить код
              </Button>
            </form>
          ) : (
            <form onSubmit={submitCode} noValidate className="space-y-4">
              <p className="text-sm text-slate-600">
                Код отправлен на <span className="font-medium text-slate-900">{email}</span>
              </p>
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
              {topError && <p className="text-sm text-red-600">{topError}</p>}
              <Button type="submit" className="w-full" isLoading={codeForm.formState.isSubmitting}>
                Войти
              </Button>
            </form>
          )}
        </CardContent>
        {step === "code" && (
          <CardFooter>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setTopError(null);
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
