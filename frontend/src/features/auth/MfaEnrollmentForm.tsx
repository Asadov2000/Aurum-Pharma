import { type FormEventHandler, type ReactNode } from "react";
import { type UseFormReturn } from "react-hook-form";
import { Button, Checkbox, FormError, Input, Label } from "@/components/ui";
import { type MfaEnrollmentSetup } from "./types";

export function MfaEnrollmentForm({
  enrollment,
  form,
  onSubmit,
  error,
}: {
  enrollment: MfaEnrollmentSetup;
  form: UseFormReturn<{ code: string; saved: boolean }>;
  onSubmit: FormEventHandler<HTMLFormElement>;
  error: ReactNode;
}): JSX.Element {
  return (
    <form onSubmit={onSubmit} noValidate className="space-y-5">
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
          aria-describedby={form.formState.errors.saved ? "recovery-codes-error" : undefined}
          {...form.register("saved")}
        />
        <span>Я сохранил резервные коды в безопасном месте</span>
      </label>
      <FormError id="recovery-codes-error">{form.formState.errors.saved?.message}</FormError>
      <div>
        <Label htmlFor="enrollment-code">Код из приложения</Label>
        <Input
          id="enrollment-code"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          className="font-mono text-base"
          invalid={Boolean(form.formState.errors.code)}
          aria-describedby={form.formState.errors.code ? "enrollment-code-error" : undefined}
          {...form.register("code")}
        />
        <FormError id="enrollment-code-error">{form.formState.errors.code?.message}</FormError>
      </div>
      {error}
      <Button type="submit" size="lg" className="w-full" isLoading={form.formState.isSubmitting}>
        Включить двухфакторную защиту
      </Button>
    </form>
  );
}
