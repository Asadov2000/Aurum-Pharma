import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { useUpdateUser } from "./queries";
import { type UserWithAssignments } from "./types";
import { employeeInitials } from "./userPresentation";

const schema = z.object({
  full_name: z.string().trim().min(1, "Введите ФИО").max(200, "Не более 200 символов"),
  phone: z.string().max(50, "Не более 50 символов"),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  user: UserWithAssignments;
  onSaved: () => void;
  onCancel: () => void;
  onDirtyChange: (dirty: boolean) => void;
}

export function UserProfileForm({ user, onSaved, onCancel, onDirtyChange }: Props): JSX.Element {
  const updateUser = useUpdateUser();
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<FormValues>({
    defaultValues: {
      full_name: user.full_name,
      phone: user.phone ?? "",
    },
  });
  const isDirty = form.formState.isDirty;

  useEffect(() => {
    onDirtyChange(isDirty);
  }, [isDirty, onDirtyChange]);

  const onSubmit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const path = issue.path[0];
        if (typeof path !== "string" || seen.has(path)) continue;
        seen.add(path);
        form.setError(path as keyof FormValues, { message: issue.message });
      }
      return;
    }

    setTopError(null);
    try {
      await updateUser.mutateAsync({
        id: user.id,
        payload: {
          full_name: parsed.data.full_name,
          phone: parsed.data.phone.trim() || null,
        },
      });
      onDirtyChange(false);
      onSaved();
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось обновить профиль"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div className="flex min-w-0 items-center gap-3 border-b border-border pb-4">
        <span
          aria-hidden="true"
          className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-primary/10 text-sm font-semibold text-primary"
        >
          {employeeInitials(user.full_name)}
        </span>
        <div className="min-w-0">
          <p className="break-words font-semibold text-foreground">{user.full_name}</p>
          <p className="break-all text-sm text-foreground-muted">{user.email}</p>
        </div>
      </div>

      <div>
        <Label htmlFor="member-full-name">ФИО</Label>
        <Input
          id="member-full-name"
          autoFocus
          autoComplete="name"
          invalid={Boolean(form.formState.errors.full_name)}
          aria-describedby={form.formState.errors.full_name ? "member-full-name-error" : undefined}
          {...form.register("full_name")}
        />
        <FormError id="member-full-name-error">
          {form.formState.errors.full_name?.message}
        </FormError>
      </div>

      <div>
        <Label htmlFor="member-phone">Телефон</Label>
        <Input
          id="member-phone"
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          placeholder="+992 00 000 00 00"
          invalid={Boolean(form.formState.errors.phone)}
          aria-describedby={form.formState.errors.phone ? "member-phone-error" : undefined}
          {...form.register("phone")}
        />
        <FormError id="member-phone-error">{form.formState.errors.phone?.message}</FormError>
      </div>

      {topError ? (
        <p
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
        >
          {topError}
        </p>
      ) : null}

      <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
        <Button type="button" variant="secondary" onClick={onCancel}>
          Отмена
        </Button>
        <Button type="submit" isLoading={form.formState.isSubmitting} disabled={!isDirty}>
          Сохранить
        </Button>
      </div>
    </form>
  );
}
