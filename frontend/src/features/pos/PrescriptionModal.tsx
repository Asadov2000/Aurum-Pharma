import { useState } from "react";
import { useForm } from "react-hook-form";

import { Button, Input, Label, Modal, Textarea } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { useAddPrescription } from "./queries";

interface FormValues {
  prescription_number: string;
  doctor_name: string;
  doctor_license: string;
  patient_name: string;
  notes: string;
}

export function PrescriptionModal({
  saleId,
  open,
  onClose,
  onSaved,
}: {
  saleId: string;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}): JSX.Element {
  const add = useAddPrescription();
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

  const onSubmit = form.handleSubmit(async (values) => {
    setTopError(null);
    const trim = (v: string) => (v.trim() ? v.trim() : null);
    try {
      await add.mutateAsync({
        saleId,
        payload: {
          prescription_number: trim(values.prescription_number),
          doctor_name: trim(values.doctor_name),
          doctor_license: trim(values.doctor_license),
          patient_name: trim(values.patient_name),
          notes: trim(values.notes),
        },
      });
      form.reset();
      onSaved();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить данные рецепта"));
    }
  });

  return (
    <Modal open={open} onClose={onClose} title="Данные рецепта">
      <form onSubmit={onSubmit} noValidate className="space-y-3">
        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
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
        {topError && <p className="text-sm text-red-600">{topError}</p>}
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
