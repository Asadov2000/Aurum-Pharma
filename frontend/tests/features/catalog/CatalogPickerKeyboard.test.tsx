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

describe("CatalogPicker — selectByNumber", () => {
  it("picks the Nth result when its digit is pressed while open", () => {
    const onChange = vi.fn();
    render(<CatalogPicker value="" onChange={onChange} selectByNumber placeholder="Поиск" />);
    const input = screen.getByPlaceholderText("Поиск");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "ас" } });

    fireEvent.keyDown(input, { key: "2" });
    expect(onChange).toHaveBeenCalledWith("c2", "Парацетамол");
  });

  it("ignores a digit greater than the number of results", () => {
    const onChange = vi.fn();
    render(<CatalogPicker value="" onChange={onChange} selectByNumber placeholder="Поиск" />);
    const input = screen.getByPlaceholderText("Поиск");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "ас" } });

    fireEvent.keyDown(input, { key: "5" }); // only 3 results exist
    expect(onChange).not.toHaveBeenCalled();
  });

  it("does not hijack digits when selectByNumber is off (default)", () => {
    const onChange = vi.fn();
    render(<CatalogPicker value="" onChange={onChange} placeholder="Поиск" />);
    const input = screen.getByPlaceholderText("Поиск");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "ас" } });

    fireEvent.keyDown(input, { key: "2" });
    expect(onChange).not.toHaveBeenCalled();
  });
});
