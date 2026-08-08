import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const items = [
  { id: "c1", brand_name: "Аспирин", dosage: "500мг", manufacturer: "Bayer" },
  { id: "c2", brand_name: "Парацетамол", dosage: "500мг", manufacturer: "GSK" },
  { id: "c3", brand_name: "Ибупрофен", dosage: "200мг", manufacturer: "Borisov" },
];

vi.mock("@/features/catalog/queries", () => ({
  useCatalogQuery: () => ({ data: { items, total: items.length } }),
}));

import { CatalogPicker } from "@/features/catalog/CatalogPicker";

describe("CatalogPicker — keyboard selection", () => {
  it("selects the first result on Enter (highlighted by default)", async () => {
    const onChange = vi.fn();
    render(<CatalogPicker value="" onChange={onChange} placeholder="Поиск" />);
    const input = screen.getByPlaceholderText("Поиск");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "ас" } });
    await screen.findByRole("option", { name: /Аспирин/ });

    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("c1", "Аспирин");
  });

  it("moves the highlight with ArrowDown and selects it on Enter", async () => {
    const onChange = vi.fn();
    render(<CatalogPicker value="" onChange={onChange} placeholder="Поиск" />);
    const input = screen.getByPlaceholderText("Поиск");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "ас" } });
    await screen.findByRole("option", { name: /Аспирин/ });

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("c2", "Парацетамол");
  });

  it("does not select on a plain digit — digits are plain input (e.g. dosage)", async () => {
    const onChange = vi.fn();
    render(<CatalogPicker value="" onChange={onChange} placeholder="Поиск" />);
    const input = screen.getByPlaceholderText("Поиск");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "ас" } });
    await screen.findByRole("option", { name: /Аспирин/ });

    fireEvent.keyDown(input, { key: "5" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("accepts digits typed into the field (dosage)", () => {
    const onChange = vi.fn();
    render(<CatalogPicker value="" onChange={onChange} placeholder="Поиск" />);
    const input = screen.getByPlaceholderText("Поиск") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Аспирин 500" } });
    expect(input.value).toBe("Аспирин 500");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("clears the visible label when its controlled value is reset externally", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <CatalogPicker value="c1" initialLabel="Аспирин" onChange={onChange} placeholder="Поиск" />,
    );
    expect(screen.getByPlaceholderText("Поиск")).toHaveValue("Аспирин");

    rerender(<CatalogPicker value="" onChange={onChange} placeholder="Поиск" />);

    expect(screen.getByPlaceholderText("Поиск")).toHaveValue("");
  });

  it("toggles a favorite without selecting the product", async () => {
    const onChange = vi.fn();
    const onFavoriteToggle = vi.fn();
    render(
      <CatalogPicker
        value=""
        onChange={onChange}
        onFavoriteToggle={onFavoriteToggle}
        favoriteCatalogIds={new Set(["c1"])}
        placeholder="Поиск"
      />,
    );
    const input = screen.getByPlaceholderText("Поиск");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "ас" } });

    fireEvent.click(await screen.findByRole("button", { name: "Убрать Аспирин из избранного" }));

    expect(onFavoriteToggle).toHaveBeenCalledWith(expect.objectContaining({ id: "c1" }));
    expect(onChange).not.toHaveBeenCalled();
    expect(input).toHaveValue("ас");
  });
});
