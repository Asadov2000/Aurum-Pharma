import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type CatalogItem } from "@/features/catalog/types";
import { SelectedProductEditor } from "@/features/pos/SelectedProductEditor";
import { catalogAmountPreview } from "@/features/pos/catalogAmountPreview";

const item: CatalogItem = {
  id: "catalog-a",
  tenant_id: "tenant-a",
  brand_name: "Аспирин",
  inn: null,
  manufacturer: null,
  form: null,
  dosage: "100 мг",
  pack_size: "№ 20",
  atx_code: null,
  dispensing_type: "otc",
  storage_type: "normal",
  category: null,
  base_price: "12.34",
  currency: "TJS",
  is_active: true,
  created_at: "2026-11-06T00:00:00Z",
  updated_at: "2026-11-06T00:00:00Z",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function submit() {
  fireEvent.submit(screen.getByRole("button", { name: "Добавить в чек" }).closest("form")!);
}

afterEach(cleanup);

describe("catalogAmountPreview", () => {
  it.each([
    ["0.10", "3", "0,30"],
    ["0.29", "100", "29,00"],
    ["12.34", "1.25", "15,43"],
    ["12.34", "1,25", "15,43"],
    ["0.01", "0.499", "0,00"],
    ["0.01", "0.500", "0,01"],
    ["1234.5", "2", "2 469,00"],
    ["999999999999.99", "1", "999 999 999 999,99"],
  ])("calculates %s × %s with exact decimal rounding", (price, quantity, expected) => {
    expect(catalogAmountPreview(price, quantity)).toBe(expected);
  });

  it.each([null, "", "-1.00", "1e2", "NaN", "1.234", "1,25", "1000000000000.00"])(
    "does not fabricate an amount for price %s",
    (price) => {
      expect(catalogAmountPreview(price, "1")).toBeNull();
    },
  );

  it.each(["", "0", "-1", "1e2", "Infinity", "1.2345", "1,2,3", "100000000000", ".5"])(
    "does not fabricate an amount for quantity %s",
    (quantity) => {
      expect(catalogAmountPreview("1.00", quantity)).toBeNull();
    },
  );

  it("rejects an estimate outside the supported money range", () => {
    expect(catalogAmountPreview("999999999999.99", "1.001")).toBeNull();
  });
});

describe("SelectedProductEditor", () => {
  it.each(["1.125", "1,125"])(
    "submits a three-decimal quantity %s without adding automatically",
    async (quantity) => {
      const add = vi.fn<() => Promise<boolean>>().mockResolvedValue(true);
      const cancel = vi.fn();
      render(<SelectedProductEditor item={item} disabled={false} onAdd={add} onCancel={cancel} />);
      expect(screen.getByText("Аспирин")).toBeInTheDocument();
      expect(screen.getByText("100 мг · № 20")).toBeInTheDocument();
      expect(screen.getByText(/Предварительно по базовой цене/)).toBeInTheDocument();
      expect(add).not.toHaveBeenCalled();
      fireEvent.change(screen.getByLabelText("Количество"), { target: { value: quantity } });
      expect(screen.getByText(`${quantity} × 12,34 = 13,88 сомони`)).toBeInTheDocument();
      submit();
      await waitFor(() => expect(add).toHaveBeenCalledExactlyOnceWith(1.125));
      expect(cancel).not.toHaveBeenCalled();
    },
  );

  it.each(["", "0", "-2", "1e3", "1.2345", "1,2,3", "100000000000"])(
    "rejects quantity %s before calling the server",
    async (quantity) => {
      const add = vi.fn<() => Promise<boolean>>().mockResolvedValue(true);
      render(<SelectedProductEditor item={item} disabled={false} onAdd={add} onCancel={vi.fn()} />);
      fireEvent.change(screen.getByLabelText("Количество"), { target: { value: quantity } });
      submit();
      expect(await screen.findByRole("alert")).toHaveTextContent("Введите количество больше нуля");
      expect(screen.getByLabelText("Количество")).toHaveAttribute("aria-invalid", "true");
      expect(add).not.toHaveBeenCalled();
    },
  );

  it("leaves a missing catalog price for server validation", async () => {
    const add = vi.fn<() => Promise<boolean>>().mockResolvedValue(true);
    render(
      <SelectedProductEditor
        item={{ ...item, base_price: null }}
        disabled={false}
        onAdd={add}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("Цена будет проверена при добавлении")).toBeInTheDocument();
    submit();
    await waitFor(() => expect(add).toHaveBeenCalledExactlyOnceWith(1));
  });

  it("blocks editing, cancellation and programmatic submission while disabled", async () => {
    const add = vi.fn<() => Promise<boolean>>().mockResolvedValue(true);
    const cancel = vi.fn();
    render(<SelectedProductEditor item={item} disabled onAdd={add} onCancel={cancel} />);
    expect(screen.getByLabelText("Количество")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Цифровой ввод количества" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Добавить в чек" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Отменить" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    await act(async () => {
      submit();
    });
    expect(add).not.toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();
  });

  it("prevents duplicate submissions while awaiting confirmation and allows a retry after refusal", async () => {
    const pending = deferred<boolean>();
    const add = vi
      .fn<() => Promise<boolean>>()
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValue(true);
    const cancel = vi.fn();
    render(<SelectedProductEditor item={item} disabled={false} onAdd={add} onCancel={cancel} />);
    fireEvent.change(screen.getByLabelText("Количество"), { target: { value: "2" } });
    await act(async () => {
      submit();
      submit();
    });
    expect(add).toHaveBeenCalledExactlyOnceWith(2);
    expect(screen.getByLabelText("Количество")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Добавить в чек" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Отменить" })).toBeDisabled();
    expect(cancel).not.toHaveBeenCalled();
    await act(async () => pending.resolve(false));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Добавить в чек" })).toBeEnabled(),
    );
    expect(screen.getByLabelText("Количество")).toHaveValue("2");
    submit();
    await waitFor(() => expect(add).toHaveBeenCalledTimes(2));
    expect(add).toHaveBeenLastCalledWith(2);
  });

  it("cancels selection without submitting an item", () => {
    const add = vi.fn<() => Promise<boolean>>().mockResolvedValue(true);
    const cancel = vi.fn();
    render(<SelectedProductEditor item={item} disabled={false} onAdd={add} onCancel={cancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    expect(cancel).toHaveBeenCalledOnce();
    expect(add).not.toHaveBeenCalled();
  });
});
