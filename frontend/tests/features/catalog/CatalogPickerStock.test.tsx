import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const items = [
  {
    id: "c1",
    brand_name: "Аспирин",
    dosage: "500мг",
    manufacturer: "Bayer",
    stock_available: "12",
  },
  { id: "c2", brand_name: "Аскорбинка", dosage: "100мг", manufacturer: "X", stock_available: "0" },
];

vi.mock("@/features/catalog/queries", () => ({
  useCatalogQuery: () => ({ data: { items, total: items.length } }),
}));

import { CatalogPicker } from "@/features/catalog/CatalogPicker";

describe("CatalogPicker — stock badge", () => {
  it("shows the stock count, and 'нет' (danger) for zero, when branchId is set", async () => {
    render(<CatalogPicker value="" onChange={vi.fn()} branchId="b-1" placeholder="Поиск" />);
    const input = screen.getByPlaceholderText("Поиск");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "ас" } });

    expect(await screen.findByText("12 шт")).toBeInTheDocument();
    const zero = screen.getByText("нет");
    expect(zero).toBeInTheDocument();
    expect(zero.className).toContain("text-danger");
  });

  it("hides the stock badge when no branchId is given", async () => {
    render(<CatalogPicker value="" onChange={vi.fn()} placeholder="Поиск" />);
    const input = screen.getByPlaceholderText("Поиск");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "ас" } });

    await screen.findByRole("option", { name: /Аспирин/ });
    expect(screen.queryByText("12 шт")).not.toBeInTheDocument();
    expect(screen.queryByText("нет")).not.toBeInTheDocument();
  });
});
