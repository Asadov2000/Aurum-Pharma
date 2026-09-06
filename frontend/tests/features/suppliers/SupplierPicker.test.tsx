import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

const searchSupplierOptions = vi.fn();

vi.mock("@/features/suppliers/api", () => ({
  searchSupplierOptions: (...args: unknown[]) => searchSupplierOptions(...args),
}));

import { SupplierPicker } from "@/features/suppliers/SupplierPicker";

function renderPicker(
  props: Partial<ComponentProps<typeof SupplierPicker>> = {},
): {
  onChange: ReturnType<typeof vi.fn>;
  rerender: (next: ComponentProps<typeof SupplierPicker>) => void;
} {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onChange = vi.fn();
  const view = render(
    <QueryClientProvider client={client}>
      <SupplierPicker value="" onChange={onChange} {...props} />
    </QueryClientProvider>,
  );
  return {
    onChange,
    rerender: (next) =>
      view.rerender(
        <QueryClientProvider client={client}>
          <SupplierPicker {...next} />
        </QueryClientProvider>,
      ),
  };
}

describe("SupplierPicker", () => {
  it("does not choose stale debounced results with Enter", async () => {
    searchSupplierOptions.mockImplementation(async (params: { q?: string }) => ({
      items:
        params.q === "новый"
          ? [{ id: "new", name: "Новый поставщик", is_active: true }]
          : [{ id: "old", name: "Старый поставщик", is_active: true }],
    }));
    const { onChange } = renderPicker();
    const input = screen.getByRole("combobox");

    fireEvent.focus(input);
    expect(await screen.findByRole("option", { name: "Старый поставщик" })).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "новый" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).not.toHaveBeenCalled();

    expect(await screen.findByRole("option", { name: "Новый поставщик" })).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(onChange).toHaveBeenCalledWith("new", "Новый поставщик"));
  });

  it("shows the saved label and exposes a touch-sized clear action", () => {
    searchSupplierOptions.mockResolvedValue({ items: [] });
    const { onChange } = renderPicker({
      value: "supplier-1",
      initialLabel: "Сино Фарм",
      clearable: true,
    });

    expect(screen.getByRole("combobox")).toHaveValue("Сино Фарм");
    const clear = screen.getByRole("button", { name: "Очистить поставщика" });
    expect(clear).toHaveClass("h-11", "w-11");
    fireEvent.click(clear);
    expect(onChange).toHaveBeenCalledWith("", "");
  });

  it("updates the label when the parent replaces the selected supplier", () => {
    searchSupplierOptions.mockResolvedValue({ items: [] });
    const { onChange, rerender } = renderPicker({
      value: "supplier-a",
      initialLabel: "Поставщик A",
    });

    expect(screen.getByRole("combobox")).toHaveValue("Поставщик A");
    rerender({
      value: "supplier-b",
      initialLabel: "Поставщик B",
      onChange,
    });

    expect(screen.getByRole("combobox")).toHaveValue("Поставщик B");
  });
});
