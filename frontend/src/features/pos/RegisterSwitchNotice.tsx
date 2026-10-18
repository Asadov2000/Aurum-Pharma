import { Button, Modal } from "@/components/ui";

export function RegisterSwitchNotice({
  unavailable,
  confirmationOpen,
  confirmBlocked,
  onCancel,
  onConfirm,
}: {
  unavailable: boolean;
  confirmationOpen: boolean;
  confirmBlocked: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}): JSX.Element {
  return (
    <>
      {unavailable ? (
        <p
          role="status"
          className="rounded-lg border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground"
        >
          Текущая касса больше недоступна. Новые операции запрещены. Сверьте результат незавершённой
          операции; если доступ отозван, обратитесь к ответственному сотруднику. Сохранённый чек
          остаётся на этой кассе.
        </p>
      ) : null}
      <Modal open={confirmationOpen} onClose={onCancel} title="Перейти на другую кассу?">
        <p className="text-sm text-foreground-secondary">
          Текущий чек останется сохранённым на этой кассе. При возвращении вы сможете продолжить
          работу с ним.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel}>
            Остаться
          </Button>
          <Button onClick={onConfirm} disabled={confirmBlocked}>
            Сохранить чек и перейти
          </Button>
        </div>
      </Modal>
    </>
  );
}
