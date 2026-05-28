import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Select, Switch } from "@/components/ui";

import { describeApiError } from "./errors";
import { useBranchesQuery, useCreateRegister, useUpdateRegister } from "./queries";
import { type PrinterType, type Register } from "./types";

const printerTypes: PrinterType[] = ["browser", "thermal_58", "thermal_80", "a4"];
const printerLabel: Record<PrinterType, string> = {
  browser: "Браузер",
  thermal_58: "Термопринтер 58 мм",
  thermal_80: "Термопринтер 80 мм",
  a4: "Принтер A4",
};

const schema = z.object({
  name: z.string().min(1, "Введите название"),
  branch_id: z.string().min(1, "Выберите точку"),
  printer_type: z.union([
    z.enum(["browser", "thermal_58", "thermal_80", "a4"]),
    z.literal(""),
  ]),
  is_active: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  register: Register | null;
  onClose: () => void;
}

export function RegisterForm({ register: row, onClose }: Props): JSX.Element {
  const isEdit = row !== null;
  const createMutation = useCreateRegister();
  const updateMutation = useUpdateRegister();
  const branches = useBranchesQuery(false);
  const [topError, setTopError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    defaultValues: {
      name: row?.name ?? "",
      branch_id: row?.branch_id ?? "",
      printer_type: row?.printer_type ?? "",
      is_active: row?.is_active ?? true,
    },
  });

  useEffect(() => {
    form.reset({
      name: row?.name ?? "",
      branch_id: row?.branch_id ?? "",
      printer_type: row?.printer_type ?? "",
      is_active: row?.is_active ?? true,
    });
  }, [row, form]);

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
    const printer = d.printer_type === "" ? null : d.printer_type;
    try {
      if (isEdit && row) {
        await updateMutation.mutateAsync({
          id: row.id,
          payload: {
            name: d.name,
            printer_type: printer,
            is_active: d.is_active,
          },
        });
      } else {
        await createMutation.mutateAsync({
          name: d.name,
          branch_id: d.branch_id,
          printer_type: printer,
        });
      }
      onClose();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить кассу"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div>
        <Label htmlFor="name">Название кассы</Label>
        <Input id="name" invalid={Boolean(form.formState.errors.name)} {...form.register("name")} />
        <FormError>{form.formState.errors.name?.message}</FormError>
      </div>
      <div>
        <Label htmlFor="branch_id">Точка</Label>
        <Select
          id="branch_id"
          disabled={isEdit}
          invalid={Boolean(form.formState.errors.branch_id)}
          {...form.register("branch_id")}
        >
          <option value="">— выберите —</option>
          {branches.data?.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </Select>
        <FormError>{form.formState.errors.branch_id?.message}</FormError>
        {isEdit && (
          <p className="mt-1 text-xs text-foreground-muted">
            Точку нельзя менять после создания кассы
          </p>
        )}
      </div>
      <div>
        <Label htmlFor="printer_type">Принтер</Label>
        <Select id="printer_type" {...form.register("printer_type")}>
          <option value="">— не задан —</option>
          {printerTypes.map((p) => (
            <option key={p} value={p}>
              {printerLabel[p]}
            </option>
          ))}
        </Select>
      </div>
      {isEdit && <Switch label="Активна" {...form.register("is_active")} />}
      {topError && <p className="text-sm text-danger">{topError}</p>}
      <div className="flex justify-end gap-2">
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
