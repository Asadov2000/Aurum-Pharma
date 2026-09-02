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

const terminalId = z
  .string()
  .trim()
  .max(64, "Не более 64 символов")
  .refine(
    (value) => value === "" || /^[A-Za-z0-9][A-Za-z0-9._:/-]*$/.test(value),
    "Используйте латинские буквы, цифры и символы . _ : / -",
  );

const schema = z.object({
  name: z.string().trim().min(1, "Введите название").max(200, "Не более 200 символов"),
  branch_id: z.string().min(1, "Выберите торговую точку"),
  printer_type: z.union([z.enum(["browser", "thermal_58", "thermal_80", "a4"]), z.literal("")]),
  card_terminal_id: terminalId,
  qr_terminal_id: terminalId,
  is_active: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  register: Register | null;
  branchName: string | null;
  onClose: () => void;
  onCancel: () => void;
  onDirtyChange: (dirty: boolean) => void;
}

export function RegisterForm({
  register: row,
  branchName,
  onClose,
  onCancel,
  onDirtyChange,
}: Props): JSX.Element {
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
      card_terminal_id: row?.card_terminal_id ?? "",
      qr_terminal_id: row?.qr_terminal_id ?? "",
      is_active: row?.is_active ?? true,
    },
  });

  useEffect(() => {
    form.reset({
      name: row?.name ?? "",
      branch_id: row?.branch_id ?? "",
      printer_type: row?.printer_type ?? "",
      card_terminal_id: row?.card_terminal_id ?? "",
      qr_terminal_id: row?.qr_terminal_id ?? "",
      is_active: row?.is_active ?? true,
    });
  }, [row, form]);

  useEffect(() => {
    onDirtyChange(form.formState.isDirty);
  }, [form.formState.isDirty, onDirtyChange]);

  const onSubmit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      let firstInvalidField: keyof FormValues | null = null;
      for (const issue of parsed.error.issues) {
        const p = issue.path[0];
        if (typeof p !== "string" || seen.has(p)) continue;
        seen.add(p);
        const field = p as keyof FormValues;
        firstInvalidField ??= field;
        form.setError(field, { message: issue.message });
      }
      if (firstInvalidField) form.setFocus(firstInvalidField);
      return;
    }
    setTopError(null);
    const d = parsed.data;
    const printer = d.printer_type === "" ? null : d.printer_type;
    const cardTerminalId = d.card_terminal_id || null;
    const qrTerminalId = d.qr_terminal_id || null;
    try {
      if (isEdit && row) {
        await updateMutation.mutateAsync({
          id: row.id,
          payload: {
            name: d.name,
            printer_type: printer,
            card_terminal_id: cardTerminalId,
            qr_terminal_id: qrTerminalId,
            is_active: d.is_active,
          },
        });
      } else {
        await createMutation.mutateAsync({
          name: d.name,
          branch_id: d.branch_id,
          printer_type: printer,
          card_terminal_id: cardTerminalId,
          qr_terminal_id: qrTerminalId,
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
        <Label htmlFor="branch_id">Торговая точка</Label>
        <Select
          id="branch_id"
          disabled={isEdit || branches.isLoading || branches.isError}
          invalid={Boolean(form.formState.errors.branch_id)}
          {...form.register("branch_id")}
        >
          <option value="">
            {branches.isLoading ? "Загрузка торговых точек…" : "Выберите торговую точку"}
          </option>
          {isEdit && row && !branches.data?.some((branch) => branch.id === row.branch_id) && (
            <option value={row.branch_id}>{branchName ?? "Текущая торговая точка"}</option>
          )}
          {branches.data?.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </Select>
        <FormError>{form.formState.errors.branch_id?.message}</FormError>
        {isEdit && (
          <p className="mt-1 text-xs text-foreground-muted">
            Торговую точку нельзя менять после создания рабочей кассы.
          </p>
        )}
        {branches.isError && (
          <div
            role="alert"
            className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          >
            <span>{describeApiError(branches.error, "Не удалось загрузить торговые точки")}</span>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => void branches.refetch()}
            >
              Повторить
            </Button>
          </div>
        )}
        {!isEdit && !branches.isLoading && !branches.isError && branches.data?.length === 0 && (
          <p className="mt-2 text-sm text-warning-foreground" role="status">
            Сначала добавьте активную торговую точку, затем создайте рабочую кассу.
          </p>
        )}
      </div>
      <div>
        <Label htmlFor="printer_type">Предпочтительный формат чека</Label>
        <Select id="printer_type" {...form.register("printer_type")}>
          <option value="">Не выбран</option>
          {printerTypes.map((p) => (
            <option key={p} value={p}>
              {printerLabel[p]}
            </option>
          ))}
        </Select>
        <p className="mt-1 text-xs leading-5 text-foreground-muted">
          Кассир сможет проверить формат перед печатью на своём устройстве.
        </p>
      </div>
      <fieldset className="space-y-3 rounded-lg border border-border bg-background p-3">
        <legend className="px-1 text-sm font-semibold">Платёжное оборудование</legend>
        <p className="text-xs leading-5 text-foreground-muted">
          Укажите постоянные ID с корпуса, чека или журнала устройства. Кассир увидит их
          автоматически. Пароли, API-ключи и секреты банка сюда вводить нельзя.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label htmlFor="card_terminal_id">Терминал для карты</Label>
            <Input
              id="card_terminal_id"
              autoComplete="off"
              maxLength={64}
              placeholder="Например, TERM-01"
              invalid={Boolean(form.formState.errors.card_terminal_id)}
              {...form.register("card_terminal_id")}
            />
            <FormError>{form.formState.errors.card_terminal_id?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="qr_terminal_id">Точка оплаты QR</Label>
            <Input
              id="qr_terminal_id"
              autoComplete="off"
              maxLength={64}
              placeholder="Например, QR-SINO-01"
              invalid={Boolean(form.formState.errors.qr_terminal_id)}
              {...form.register("qr_terminal_id")}
            />
            <FormError>{form.formState.errors.qr_terminal_id?.message}</FormError>
          </div>
        </div>
      </fieldset>
      {isEdit && <Switch label="Рабочая касса активна" {...form.register("is_active")} />}
      {topError && (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
        >
          {topError}
        </div>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onCancel}>
          Отмена
        </Button>
        <Button
          type="submit"
          isLoading={form.formState.isSubmitting}
          disabled={
            !isEdit && (branches.isLoading || branches.isError || branches.data?.length === 0)
          }
        >
          {isEdit ? "Сохранить изменения" : "Добавить кассу"}
        </Button>
      </div>
    </form>
  );
}
