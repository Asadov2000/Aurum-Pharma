import { render, screen } from "@testing-library/react";

import { ReceiptDocument } from "@/features/pos/ReceiptPrintModal";
import { type ReceiptData } from "@/features/pos/types";

const BASE_RECEIPT: ReceiptData = {
  sale_id: "11111111-1111-4111-8111-111111111111",
  is_refund: false,
  status: "completed",
  pharmacy_name: "Аптека Сино",
  branch_name: "Центр",
  branch_address: "Душанбе",
  branch_license: "LIC-1",
  receipt_number: "01-01-000101",
  original_receipt_number: null,
  datetime: "2026-08-29T12:00:00+05:00",
  cashier_name: "Кассир",
  items: [
    {
      position: 1,
      name: "Парацетамол 500 мг",
      qty: "1.000",
      unit_price: "6.50",
      discount_amount: "0.00",
      total_price: "6.50",
    },
  ],
  discount_total: "0.00",
  total: "6.50",
  currency: "TJS",
  payments: [{ method: "cash", amount: "6.50" }],
  paid_total: "10.00",
  change: "3.50",
};

describe("ReceiptDocument", () => {
  it("does not invent tender or change when the historical receipt has neither", () => {
    render(
      <ReceiptDocument
        data={{ ...BASE_RECEIPT, paid_total: null, change: null }}
        contentWidth="80mm"
      />,
    );
    expect(screen.getByText("ИТОГО")).toBeInTheDocument();
    expect(screen.getByText("Наличные")).toBeInTheDocument();
    expect(screen.queryByText("Принято")).not.toBeInTheDocument();
    expect(screen.queryByText("Сдача")).not.toBeInTheDocument();
  });
  it("shows sale tender, change and purchase footer", () => {
    render(<ReceiptDocument data={BASE_RECEIPT} contentWidth="80mm" />);

    expect(screen.getByText("ИТОГО")).toBeInTheDocument();
    expect(screen.getByText("Принято")).toBeInTheDocument();
    expect(screen.getByText("Сдача")).toBeInTheDocument();
    expect(screen.getByText("Спасибо за покупку!")).toBeInTheDocument();
    expect(screen.getByText("1 × 6.50")).toBeInTheDocument();
  });

  it.each([
    ["шт.", "5.000", "5 таблеток"],
    ["блист.", "2.000", "2 блистера по 10 табл."],
    ["упак.", "1.000", "1 упаковка: 30 табл."],
  ])("prints the historical quantity and unit %s", (unitLabel, qty, description) => {
    render(
      <ReceiptDocument
        data={{
          ...BASE_RECEIPT,
          items: [
            {
              ...BASE_RECEIPT.items[0],
              qty,
              unit_label: unitLabel,
              quantity_description: description,
            },
          ],
        }}
        contentWidth="58mm"
      />,
    );

    expect(screen.getByText(description)).toBeInTheDocument();
    expect(screen.getByText(`${Number(qty)} ${unitLabel} × 6.50`)).toBeInTheDocument();
  });

  it("identifies a refund without sale-only wording", () => {
    render(
      <ReceiptDocument
        data={{
          ...BASE_RECEIPT,
          sale_id: "22222222-2222-4222-8222-222222222222",
          is_refund: true,
          receipt_number: "01-01-000102",
          original_receipt_number: "01-01-000101",
          paid_total: "6.50",
          change: "0.00",
        }}
        contentWidth="80mm"
      />,
    );

    expect(screen.getByText("ВОЗВРАТ")).toBeInTheDocument();
    expect(screen.getByText("Исходный чек № 01-01-000101")).toBeInTheDocument();
    expect(screen.getByText("ВОЗВРАЩЕНО")).toBeInTheDocument();
    expect(screen.queryByText("Принято")).not.toBeInTheDocument();
    expect(screen.queryByText("Сдача")).not.toBeInTheDocument();
    expect(screen.queryByText("Спасибо за покупку!")).not.toBeInTheDocument();
    expect(screen.getByText("Средства возвращены по исходному чеку.")).toBeInTheDocument();
  });
});
