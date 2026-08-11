import { Link, useSearch } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { BrandMark } from "@/components/layout/BrandMark";
import { Button, Card, CardContent, FormError, Input, Label } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { useActivatePlatformStaffAccount } from "./queries";

const schema = z
  .object({
    password: z
      .string()
      .min(12, "Минимум 12 символов")
      .max(128, "Максимум 128 символов")
      .regex(/[a-zа-яё]/, "Добавьте строчную букву")
      .regex(/[A-ZА-ЯЁ]/, "Добавьте заглавную букву")
      .regex(/\d/, "Добавьте цифру"),
    confirmation: z.string(),
  })
  .refine((values) => values.password === values.confirmation, {
    path: ["confirmation"],
    message: "Пароли не совпадают",
  });

type FormValues = z.infer<typeof schema>;

export function PlatformActivationPage(): JSX.Element {
  const search = useSearch({ strict: false }) as { token?: string };
  const [token] = useState(search.token ?? "");
  const [error, setError] = useState<string | null>(null);
  const [completed, setCompleted] = useState(false);
  const activation = useActivatePlatformStaffAccount();
  const form = useForm<FormValues>({ defaultValues: { password: "", confirmation: "" } });

  useEffect(() => {
    if (search.token) {
      window.history.replaceState(window.history.state, "", "/activate-platform");
    }
  }, [search.token]);

  const submit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (field === "password" || field === "confirmation") {
          form.setError(field, { message: issue.message });
        }
      }
      return;
    }
    if (!token) {
      setError("Ссылка активации неполная или устарела.");
      return;
    }
    setError(null);
    try {
      await activation.mutateAsync({ token, password: parsed.data.password });
      form.reset({ password: "", confirmation: "" });
      setCompleted(true);
    } catch (caught) {
      setError(describeApiError(caught, "Ссылка недействительна или срок её действия истёк."));
    }
  });

  return (
    <main className="grid min-h-screen place-items-center bg-app px-4 py-8">
      <div className="w-full max-w-md space-y-6">
        <div className="flex justify-center">
          <BrandMark />
        </div>
        <Card>
          <CardContent className="p-6 sm:p-8">
            {completed ? (
              <div className="space-y-4 text-center">
                <h1 className="font-display text-2xl font-semibold">Аккаунт активирован</h1>
                <p className="text-sm leading-5 text-foreground-secondary">
                  Пароль установлен. Полномочия платформы появятся только после отдельного
                  назначения разработчиком.
                </p>
                <Link
                  to="/login"
                  className="inline-flex h-[var(--control-height-md)] items-center rounded-md bg-primary px-4 text-sm font-semibold text-primary-foreground"
                >
                  Перейти ко входу
                </Link>
              </div>
            ) : (
              <form onSubmit={submit} noValidate className="space-y-4">
                <div>
                  <h1 className="font-display text-2xl font-semibold">Активация аккаунта</h1>
                  <p className="mt-1 text-sm leading-5 text-foreground-secondary">
                    Создайте пароль для аккаунта команды Aurum Pharma.
                  </p>
                </div>
                <div>
                  <Label htmlFor="activation-password">Новый пароль</Label>
                  <Input
                    id="activation-password"
                    type="password"
                    autoComplete="new-password"
                    invalid={Boolean(form.formState.errors.password)}
                    {...form.register("password")}
                  />
                  <FormError>{form.formState.errors.password?.message}</FormError>
                </div>
                <div>
                  <Label htmlFor="activation-confirmation">Повторите пароль</Label>
                  <Input
                    id="activation-confirmation"
                    type="password"
                    autoComplete="new-password"
                    invalid={Boolean(form.formState.errors.confirmation)}
                    {...form.register("confirmation")}
                  />
                  <FormError>{form.formState.errors.confirmation?.message}</FormError>
                </div>
                {error && (
                  <p
                    role="alert"
                    className="rounded-md border border-danger/30 bg-danger-subtle p-3 text-sm"
                  >
                    {error}
                  </p>
                )}
                <Button type="submit" className="w-full" isLoading={form.formState.isSubmitting}>
                  Активировать аккаунт
                </Button>
              </form>
            )}
          </CardContent>
        </Card>
      </div>
    </main>
  );
}

export default PlatformActivationPage;
