import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Select, Switch, Textarea } from "@/components/ui";

import { describeApiError } from "./errors";
import { useCreateBranch, useUpdateBranch } from "./queries";
import { type Branch, type BranchType } from "./types";

const branchTypes: BranchType[] = ["pharmacy", "pharmacy_post", "kiosk"];
const branchTypeLabel: Record<BranchType, string> = {
  pharmacy: "Аптека",
  pharmacy_post: "Аптечный пункт",
  kiosk: "Киоск",
};

const schema = z.object({
  name: z.string().min(1, "Введите название"),
  branch_type: z.enum(["pharmacy", "pharmacy_post", "kiosk"]),
  address: z.string().optional(),
  license_number: z.string().optional(),
  license_expires_at: z.string().optional(),
  receipt_line1: z.string().max(200, "Не больше 200 символов").optional(),
  receipt_line2: z.string().max(200, "Не больше 200 символов").optional(),
  receipt_phone: z.string().max(50, "Не больше 50 символов").optional(),
  receipt_inn_or_tin: z.string().max(50, "Не больше 50 символов").optional(),
  is_active: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  branch: Branch | null;
  onClose: () => void;
}

export function BranchForm({ branch, onClose }: Props): JSX.Element {
  const isEdit = branch !== null;
  const createMutation = useCreateBranch();
  const updateMutation = useUpdateBranch();
  const [topError, setTopError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    defaultValues: {
      name: branch?.name ?? "",
      branch_type: branch?.branch_type ?? "pharmacy",
      address: branch?.address ?? "",
      license_number: branch?.license_number ?? "",
      license_expires_at: branch?.license_expires_at ?? "",
      receipt_line1: branch?.receipt_header?.line1 ?? "",
      receipt_line2: branch?.receipt_header?.line2 ?? "",
      receipt_phone: branch?.receipt_header?.phone ?? "",
      receipt_inn_or_tin: branch?.receipt_header?.inn_or_tin ?? "",
      is_active: branch?.is_active ?? true,
    },
  });

  useEffect(() => {
    form.reset({
      name: branch?.name ?? "",
      branch_type: branch?.branch_type ?? "pharmacy",
      address: branch?.address ?? "",
      license_number: branch?.license_number ?? "",
      license_expires_at: branch?.license_expires_at ?? "",
      receipt_line1: branch?.receipt_header?.line1 ?? "",
      receipt_line2: branch?.receipt_header?.line2 ?? "",
      receipt_phone: branch?.receipt_header?.phone ?? "",
      receipt_inn_or_tin: branch?.receipt_header?.inn_or_tin ?? "",
      is_active: branch?.is_active ?? true,
    });
  }, [branch, form]);

  const onSubmit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const p = issue.path[0];
        if (typeof p !== "string" || seen.has(p)) continue;
        seen.add(p);
        form.setError(p as keyof FormValues, { message: issue.message });
      }
      return;
    }
    setTopError(null);
    const d = parsed.data;
    const trim = (v: string | undefined) => (v && v.trim() !== "" ? v.trim() : null);
    const receiptLine1 = trim(d.receipt_line1);
    const receiptHeader = receiptLine1
      ? {
          line1: receiptLine1,
          line2: trim(d.receipt_line2),
          phone: trim(d.receipt_phone),
          inn_or_tin: trim(d.receipt_inn_or_tin),
          demo_notice: branch?.receipt_header?.demo_notice ?? null,
        }
      : null;
    try {
      if (isEdit && branch) {
        await updateMutation.mutateAsync({
          id: branch.id,
          payload: {
            name: d.name,
            branch_type: d.branch_type,
            address: trim(d.address),
            license_number: trim(d.license_number),
            license_expires_at: trim(d.license_expires_at),
            receipt_header: receiptHeader,
            is_active: d.is_active,
          },
        });
      } else {
        await createMutation.mutateAsync({
          name: d.name,
          branch_type: d.branch_type,
          address: trim(d.address),
          license_number: trim(d.license_number),
          license_expires_at: trim(d.license_expires_at),
          ...(receiptHeader ? { receipt_header: receiptHeader } : {}),
        });
      }
      onClose();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить точку"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div>
        <Label htmlFor="name">Название</Label>
        <Input id="name" invalid={Boolean(form.formState.errors.name)} {...form.register("name")} />
        <FormError>{form.formState.errors.name?.message}</FormError>
      </div>
      <div>
        <Label htmlFor="branch_type">Тип</Label>
        <Select id="branch_type" {...form.register("branch_type")}>
          {branchTypes.map((t) => (
            <option key={t} value={t}>
              {branchTypeLabel[t]}
            </option>
          ))}
        </Select>
      </div>
      <div>
        <Label htmlFor="address">Адрес</Label>
        <Textarea id="address" {...form.register("address")} />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <Label htmlFor="license_number">Номер лицензии</Label>
          <Input id="license_number" {...form.register("license_number")} />
        </div>
        <div>
          <Label htmlFor="license_expires_at">Истекает</Label>
          <Input id="license_expires_at" type="date" {...form.register("license_expires_at")} />
        </div>
      </div>
      <div className="space-y-4 border-t border-border pt-4">
        <div>
          <p className="text-sm font-medium text-foreground">Реквизиты на чеке</p>
          <p className="mt-1 text-xs leading-5 text-foreground-muted">
            Первая строка обязательна перед запуском пробного периода.
          </p>
        </div>
        <div>
          <Label htmlFor="receipt_line1">Название организации</Label>
          <Input
            id="receipt_line1"
            invalid={Boolean(form.formState.errors.receipt_line1)}
            {...form.register("receipt_line1")}
          />
          <FormError>{form.formState.errors.receipt_line1?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="receipt_line2">Название точки</Label>
          <Input
            id="receipt_line2"
            invalid={Boolean(form.formState.errors.receipt_line2)}
            {...form.register("receipt_line2")}
          />
          <FormError>{form.formState.errors.receipt_line2?.message}</FormError>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="receipt_phone">Телефон</Label>
            <Input
              id="receipt_phone"
              invalid={Boolean(form.formState.errors.receipt_phone)}
              {...form.register("receipt_phone")}
            />
            <FormError>{form.formState.errors.receipt_phone?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="receipt_inn_or_tin">ИНН / TIN</Label>
            <Input
              id="receipt_inn_or_tin"
              invalid={Boolean(form.formState.errors.receipt_inn_or_tin)}
              {...form.register("receipt_inn_or_tin")}
            />
            <FormError>{form.formState.errors.receipt_inn_or_tin?.message}</FormError>
          </div>
        </div>
      </div>
      {isEdit && <Switch label="Активна" {...form.register("is_active")} />}
      {topError && (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm leading-5 text-danger-foreground"
        >
          {topError}
        </div>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button type="submit" isLoading={form.formState.isSubmitting}>
          {isEdit ? "Сохранить" : "Создать"}
        </Button>
      </div>
    </form>
  );
}
