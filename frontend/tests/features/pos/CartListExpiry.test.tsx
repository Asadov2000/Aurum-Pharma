import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CartList } from "@/features/pos/CartList";
import { type SaleItem } from "@/features/pos/types";

function makeItem(over: Partial<SaleItem>): SaleItem {
  return {
    id: "i",
    sale_id: "s",
    catalog_id: "c",
    batch_id: "b",
    qty: "1",
    unit_price: "10.00",
    total_price: "10.00",
    currency: "TJS",
    discount_amount: "0",
    position: 1,
    ...over,
  };
}

function renderCart(items: SaleItem[]) {
  return render(
    <CartList
      items={items}
      nameById={{ c: "Аспирин" }}
      currency="TJS"
      editable
      onQtyChange={vi.fn()}
      onDelete={vi.fn()}
    />,
  );
}

describe("CartList — batch + expiry", () => {
  it("shows batch number, expiry date and a days-to-expiry hint", () => {
    renderCart([
      makeItem({ id: "i1", batch_number: "L0001-1", expires_at: "2026-12-31", days_to_expiry: 200 }),
    ]);
    expect(screen.getByText("L0001-1")).toBeInTheDocument();
    expect(screen.getByText(/через 200 дн\./)).toBeInTheDocument();
  });

  it("colours an expired line in danger", () => {
    renderCart([
      makeItem({ id: "i1", batch_number: "OLD-1", expires_at: "2020-01-01", days_to_expiry: -5 }),
    ]);
    const hint = screen.getByText(/просрочена 5 дн\./);
    expect(hint).toBeInTheDocument();
    expect(hint.className).toContain("text-danger");
  });

  it("disambiguates FEFO-split lines of the same product by batch number", () => {
    renderCart([
      makeItem({ id: "i1", catalog_id: "c", batch_number: "B-A", days_to_expiry: 60 }),
      makeItem({ id: "i2", catalog_id: "c", batch_number: "B-B", days_to_expiry: 150 }),
    ]);
    expect(screen.getByText("B-A")).toBeInTheDocument();
    expect(screen.getByText("B-B")).toBeInTheDocument();
    expect(screen.getAllByTestId("cart-item")).toHaveLength(2);
  });
});
