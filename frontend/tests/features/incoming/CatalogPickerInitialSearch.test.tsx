import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/features/catalog/pickerQueries", () => ({
  useCatalogPickerQuery: () => ({ data: { items: [] }, isFetching: false }),
}));

import { CatalogPicker } from "@/features/catalog/CatalogPicker";

describe("CatalogPicker supplier name prefill", () => {
  it("keeps the supplier name as editable search until the user changes it", () => {
    const onChange = vi.fn();
    render(<CatalogPicker value="" initialSearch="Парацетамол 500 мг" onChange={onChange} />);

    const search = screen.getByRole("combobox");
    expect(search).toHaveValue("Парацетамол 500 мг");
    fireEvent.change(search, { target: { value: "Парацетамол" } });
    expect(search).toHaveValue("Парацетамол");
    expect(onChange).not.toHaveBeenCalled();
  });
});
