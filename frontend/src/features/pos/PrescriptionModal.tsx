import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Input, Label, Modal, Textarea } from "@/components/ui";

import { type PrescriptionLogPayload } from "./types";

interface FormValues {
  prescription_number: string;
  doctor_name: string;
  doctor_license: string;
  patient_name: string;
  notes: string;
}

const prescriptionSchema = z.object({
  prescription_number: z.string().max(500),
  doctor_name: z.string().max(500),
  doctor_license: z.string().max(500),
  patient_name: z.string().max(500),
  notes: z.string().max(2000),
});

export function PrescriptionModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (payload: PrescriptionLogPayload) => void;
}): JSX.Element {
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<FormValues>({
    defaultValues: {
      prescription_number: "",
      doctor_name: "",
      doctor_license: "",
      patient_name: "",
      notes: "",
    },
  });

  const onSubmit = form.handleSubmit((values) => {
    setTopError(null);
    const parsed = prescriptionSchema.safeParse(values);
    if (!parsed.success) {
      setTopError("Проверьте длину заполненных полей рецепта.");
      return;
    }
    const trim = (v: string) => (v.trim() ? v.trim() : null);
    onSaved({
      prescription_number: trim(parsed.data.prescription_number),
      doctor_name: trim(parsed.data.doctor_name),
      doctor_license: trim(parsed.data.doctor_license),
      patient_name: trim(parsed.data.patient_name),
      notes: trim(parsed.data.notes),
    });
    form.reset();
  });

  return (
    <Modal open={open} onClose={onClose} title="Данные рецепта">
      <form onSubmit={onSubmit} noValidate className="space-y-3">
        <p className="rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
          В чеке есть рецептурная позиция — заполните данные рецепта.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="prescription_number">Номер рецепта</Label>
            <Input id="prescription_number" {...form.register("prescription_number")} />
          </div>
          <div>
            <Label htmlFor="patient_name">Пациент</Label>
            <Input id="patient_name" {...form.register("patient_name")} />
          </div>
          <div>
            <Label htmlFor="doctor_name">Врач</Label>
            <Input id="doctor_name" {...form.register("doctor_name")} />
          </div>
          <div>
            <Label htmlFor="doctor_license">Лицензия врача</Label>
            <Input id="doctor_license" {...form.register("doctor_license")} />
          </div>
        </div>
        <div>
          <Label htmlFor="rx_notes">Комментарий</Label>
          <Textarea id="rx_notes" {...form.register("notes")} />
        </div>
        {topError && <p className="text-sm text-danger">{topError}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button type="submit" isLoading={form.formState.isSubmitting}>
            Сохранить
          </Button>
        </div>
      </form>
    </Modal>
  );
}
