import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Modal } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { useInvitePlatformStaffAccount } from "./queries";

const schema = z.object({
  full_name: z.string().trim().min(2, "Введите имя").max(200, "Слишком длинное имя"),
  email: z.string().trim().min(1, "Введите email").email("Некорректный email"),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  open: boolean;
  onClose: () => void;
}

export function PlatformInvitationModal({ open, onClose }: Props): JSX.Element | null {
  const invitation = useInvitePlatformStaffAccount();
  const [error, setError] = useState<string | null>(null);
  const [activationUrl, setActivationUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const form = useForm<FormValues>({ defaultValues: { full_name: "", email: "" } });

  useEffect(() => {
    if (!open) {
      form.reset({ full_name: "", email: "" });
      setError(null);
      setActivationUrl(null);
      setCopied(false);
    }
  }, [form, open]);

  const submit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (field === "full_name" || field === "email") {
          form.setError(field, { message: issue.message });
        }
      }
      return;
    }
    setError(null);
    try {
      const result = await invitation.mutateAsync(parsed.data);
      if (result.activation_token) {
        setActivationUrl(
          `${window.location.origin}/activate-platform#token=${encodeURIComponent(result.activation_token)}`,
        );
      } else {
        onClose();
      }
    } catch (caught) {
      setError(describeApiError(caught, "Не удалось создать приглашение."));
    }
  });

  const copyActivationUrl = async () => {
    if (!activationUrl) return;
    setError(null);
    try {
      await navigator.clipboard.writeText(activationUrl);
      setCopied(true);
    } catch {
      setError("Не удалось скопировать ссылку. Выделите и скопируйте её вручную.");
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Пригласить в команду Aurum">
      {activationUrl ? (
        <div className="space-y-4">
          <p className="text-sm leading-5 text-foreground-secondary">
            Аккаунт создан без полномочий. Передайте эту одноразовую ссылку сотруднику по
            защищённому каналу. Ссылка действует 24 часа.
          </p>
          <div>
            <Label htmlFor="platform-activation-url">Ссылка активации</Label>
            <Input id="platform-activation-url" value={activationUrl} readOnly />
          </div>
          {error && (
            <p
              role="alert"
              className="rounded-md border border-danger/30 bg-danger-subtle p-3 text-sm"
            >
              {error}
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="secondary" onClick={copyActivationUrl}>
              {copied ? "Скопировано" : "Копировать ссылку"}
            </Button>
            <Button type="button" onClick={onClose}>
              Готово
            </Button>
          </div>
        </div>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-4">
          <p className="text-sm leading-5 text-foreground-secondary">
            Приглашение не выдаёт доступ разработчика или администратора. Такие полномочия
            назначаются отдельно разработчиком после активации аккаунта.
          </p>
          <div>
            <Label htmlFor="platform-full-name">Имя сотрудника</Label>
            <Input
              id="platform-full-name"
              autoComplete="name"
              invalid={Boolean(form.formState.errors.full_name)}
              {...form.register("full_name")}
            />
            <FormError>{form.formState.errors.full_name?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="platform-email">Email</Label>
            <Input
              id="platform-email"
              type="email"
              autoComplete="email"
              invalid={Boolean(form.formState.errors.email)}
              {...form.register("email")}
            />
            <FormError>{form.formState.errors.email?.message}</FormError>
          </div>
          {error && (
            <p
              role="alert"
              className="rounded-md border border-danger/30 bg-danger-subtle p-3 text-sm"
            >
              {error}
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              Отмена
            </Button>
            <Button type="submit" isLoading={form.formState.isSubmitting}>
              Создать приглашение
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}
