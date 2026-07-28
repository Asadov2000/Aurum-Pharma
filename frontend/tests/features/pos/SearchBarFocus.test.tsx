import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const items = [{ id: "c1", brand_name: "Аспирин", dosage: "500мг", manufacturer: "Bayer" }];

vi.mock("@/features/catalog/queries", () => ({
  useCatalogQuery: () => ({ data: { items, total: items.length } }),
}));

import { SearchBar } from "@/features/pos/SearchBar";

describe("SearchBar — keyboard focus flow", () => {
  it("moves focus to the quantity field after a product is picked", async () => {
    render(<SearchBar onAdd={vi.fn()} />);
    const input = screen.getByPlaceholderText(/Поиск товара/);
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "Асп" } });

    const result = await screen.findByRole("option", { name: /Аспирин/ });
    fireEvent.click(result);

    const qty = screen.getByLabelText("Количество");
    await waitFor(() => expect(qty).toHaveFocus());
  });

  it("moves focus to the quantity field when a product is picked with Enter", async () => {
    render(<SearchBar onAdd={vi.fn()} />);
    const input = screen.getByPlaceholderText(/Поиск товара/);
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "Асп" } });

    await screen.findByRole("option", { name: /Аспирин/ });
    fireEvent.keyDown(input, { key: "Enter" });

    const qty = screen.getByLabelText("Количество");
    await waitFor(() => expect(qty).toHaveFocus());
  });

  it("keeps the selected product when adding it fails", async () => {
    const onAdd = vi.fn().mockResolvedValue(false);
    render(<SearchBar onAdd={onAdd} />);
    const input = screen.getByPlaceholderText(/Поиск товара/);
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "Асп" } });
    fireEvent.click(await screen.findByRole("option", { name: /Аспирин/ }));

    const qty = screen.getByLabelText("Количество");
    fireEvent.keyDown(qty, { key: "Enter" });

    await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1));
    expect(input).toHaveValue("Аспирин");
  });
});
