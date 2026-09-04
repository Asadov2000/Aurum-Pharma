import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
}));

vi.mock("@/features/inventory/queries", () => ({
  useWriteOff: () => ({ mutateAsync: mocks.mutateAsync }),
}));

import { WriteOffForm } from "@/features/inventory/WriteOffForm";

function setOnline(value: boolean): void {
  Object.defineProperty(navigator, "onLine", { configurable: true, value });
}

function renderForm(onClose = vi.fn(), purchasePrice: string | null = "4.00") {
  render(
    <WriteOffForm
      batchId="00000000-0000-0000-0000-000000000001"
      maxQty="5.000"
      purchasePrice={purchasePrice}
      currency="TJS"
      productName="Парацетамол"
      batchNumber="LOT-1"
      onClose={onClose}
    />,
  );
  return { onClose };
}

function fillValidForm(): void {
  fireEvent.change(screen.getByLabelText("Количество"), { target: { value: "2,5" } });
  fireEvent.change(screen.getByLabelText("Причина"), { target: { value: "damaged" } });
  fireEvent.change(screen.getByLabelText("Комментарий"), {
    target: { value: "Повреждена упаковка" },
  });
}

describe("WriteOffForm", () => {
  beforeEach(() => {
    mocks.mutateAsync.mockReset();
    setOnline(true);
  });

  it("requires an explicit reason and enforces the available stock", async () => {
    renderForm();
    expect(screen.getByLabelText("Причина")).toHaveValue("");

    fireEvent.change(screen.getByLabelText("Количество"), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText("Причина"), { target: { value: "other" } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить списание" }));

    expect(await screen.findByText("Доступно не более 5")).toBeInTheDocument();
    expect(mocks.mutateAsync).not.toHaveBeenCalled();
  });

  it("reuses the operation id after a lost response", async () => {
    mocks.mutateAsync.mockRejectedValueOnce(new Error("response lost")).mockResolvedValueOnce({
      id: "00000000-0000-4000-8000-000000000099",
      batch_id: "00000000-0000-0000-0000-000000000001",
      qty: "2.500",
      reason: "damaged",
      comment: "Повреждена упаковка",
      amount: "10.00",
      currency: "TJS",
      created_at: "2026-08-30T10:00:00Z",
    });
    const { onClose } = renderForm();
    fillValidForm();

    fireEvent.click(screen.getByRole("button", { name: "Подтвердить списание" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось списать товар");
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить списание" }));

    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledTimes(2));
    const firstOperationId = mocks.mutateAsync.mock.calls[0]?.[0].payload.operation_id;
    const secondOperationId = mocks.mutateAsync.mock.calls[1]?.[0].payload.operation_id;
    expect(firstOperationId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(secondOperationId).toBe(firstOperationId);
    expect(mocks.mutateAsync.mock.calls[0]?.[0].payload).toMatchObject({
      qty: "2.5",
      reason: "damaged",
      comment: "Повреждена упаковка",
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Товар списан");
    expect(screen.getByText("Повреждение")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Готово" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("blocks write-off while the workstation is offline", () => {
    setOnline(false);
    renderForm();

    expect(screen.getByText(/остаток не будет изменён офлайн/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подтвердить списание" })).toBeDisabled();
  });

  it("keeps the form usable without revealing a hidden purchase price", () => {
    renderForm(vi.fn(), null);
    fillValidForm();

    expect(screen.queryByText(/Сумма списания по закупочной цене/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подтвердить списание" })).toBeEnabled();
  });
});
