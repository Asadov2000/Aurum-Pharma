import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { useUpdateUser } from "./queries";
import { type UserWithAssignments } from "./types";

const schema = z.object({
  full_name: z.string().trim().min(1, "Введите ФИО").max(200, "Не более 200 символов"),
  phone: z.string().max(50, "Не более 50 символов"),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  user: UserWithAssignments;
  onClose: () => void;
}

export function UserProfileForm({ user, onClose }: Props): JSX.Element {
  const updateUser = useUpdateUser();
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<FormValues>({
    defaultValues: {
      full_name: user.full_name,
      phone: user.phone ?? "",
    },
  });

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
      onClose();
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось обновить профиль"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div>
        <Label htmlFor="member-full-name">ФИО</Label>
        <Input
          id="member-full-name"
          invalid={Boolean(form.formState.errors.full_name)}
          {...form.register("full_name")}
        />
        <FormError>{form.formState.errors.full_name?.message}</FormError>
      </div>

      <div>
        <Label htmlFor="member-phone">Телефон</Label>
        <Input
          id="member-phone"
          type="tel"
          invalid={Boolean(form.formState.errors.phone)}
          {...form.register("phone")}
        />
        <FormError>{form.formState.errors.phone?.message}</FormError>
      </div>

      {topError && <p className="text-sm text-danger">{topError}</p>}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button type="submit" isLoading={form.formState.isSubmitting}>
          Сохранить
        </Button>
      </div>
    </form>
  );
}
