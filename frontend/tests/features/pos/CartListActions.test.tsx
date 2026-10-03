import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CartList } from "@/features/pos/CartList";
import { type SaleItem } from "@/features/pos/types";
import {
  defaultQuickActionPreferences,
  type QuickActionMenuPreferences,
} from "@/features/settings/quickActions";

const navigate = vi.hoisted(() => vi.fn());
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }));

function makeItem(overrides: Partial<SaleItem> = {}): SaleItem {
  return {
    id: "item-a",
    sale_id: "sale-a",
    catalog_id: "catalog-a",
    batch_id: "batch-a",
    qty: "2",
    unit_price: "10.00",
    total_price: "20.00",
    currency: "TJS",
    discount_amount: "0",
    position: 1,
    batch_number: "B-A",
    expires_at: "2027-01-01",
    ...overrides,
  };
}

describe("CartList — row actions", () => {
  const onQtyChange = vi.fn<(id: string, qty: number) => void>();
  const onDelete = vi.fn<(id: string) => void>();
  const writeText = vi.fn<(value: string) => Promise<void>>();
  const props = {
    items: [makeItem()],
    nameById: { "catalog-a": "Аспирин" },
    currency: "TJS",
    editable: true,
    onQtyChange,
    onDelete,
  };
  let config: QuickActionMenuPreferences;
  const preferences = () => ({ config, scope: "tenant:user" });
  const trigger = (row: HTMLElement = screen.getByTestId("cart-item")) =>
    within(row).getByRole("button", { name: /^Действия / });
  const open = (row?: HTMLElement) => fireEvent.click(trigger(row));

  beforeEach(() => {
    config = defaultQuickActionPreferences("pos_cart");
    onQtyChange.mockReset();
    onDelete.mockReset();
    navigate.mockReset();
    writeText.mockReset().mockResolvedValue();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("keeps standalone rows and their existing controls without context menus", () => {
    render(<CartList {...props} />);
    const row = screen.getByTestId("cart-item");
    expect(row).not.toHaveAttribute("data-context-actions");
    expect(row).not.toHaveAttribute("tabindex");
    expect(within(row).queryByRole("button", { name: /^Действия / })).not.toBeInTheDocument();
    expect(fireEvent.contextMenu(row)).toBe(true);
    fireEvent.click(within(row).getByRole("button", { name: "Увеличить количество" }));
    expect(onQtyChange).toHaveBeenCalledWith("item-a", 3);
    fireEvent.click(within(row).getByRole("button", { name: "Удалить Аспирин" }));
    expect(onDelete).toHaveBeenCalledWith("item-a");
  });

  it("targets the exact FEFO batch row and never adds a menu to its product header", async () => {
    render(
      <CartList
        {...props}
        items={[
          makeItem(),
          makeItem({ id: "item-b", batch_id: "batch-b", batch_number: "B-B", qty: "4" }),
        ]}
        quickActions={preferences()}
      />,
    );
    const rows = screen.getAllByTestId("cart-item");
    expect(rows).toHaveLength(2);
    const header = screen.getByText("Аспирин").parentElement!;
    expect(header).not.toHaveAttribute("data-context-actions");
    expect(within(header).queryByRole("button", { name: /^Действия / })).not.toBeInTheDocument();
    expect(fireEvent.contextMenu(header)).toBe(true);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(fireEvent.contextMenu(rows[1], { clientX: 100, clientY: 100 })).toBe(false);
    expect(screen.getByRole("menu")).toHaveTextContent("Партия B-B · до 01.01.2027");
    expect(onQtyChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("menuitem", { name: "Увеличить количество" }));
    expect(onQtyChange).toHaveBeenCalledExactlyOnceWith("item-b", 5);
    open(rows[1]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Скопировать номер партии" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("B-B"));
    open(rows[1]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Скопировать название" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Аспирин"));
    open(rows[1]);
    const remove = screen.getByRole("menuitem", { name: "Удалить из чека" });
    expect(remove).toHaveClass("text-danger");
    fireEvent.click(remove);
    expect(onDelete).toHaveBeenCalledExactlyOnceWith("item-b");
  });

  it("matches the existing stepper for fractional quantities and hides decrease at one", () => {
    const fractional = { ...props, items: [makeItem({ qty: "1.5" })] };
    const { rerender } = render(<CartList {...fractional} quickActions={preferences()} />);
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Увеличить количество" }));
    expect(onQtyChange).toHaveBeenLastCalledWith("item-a", 2.5);
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Уменьшить количество" }));
    expect(onQtyChange).toHaveBeenLastCalledWith("item-a", 1);
    rerender(<CartList {...props} items={[makeItem({ qty: "1" })]} quickActions={preferences()} />);
    open();
    expect(
      screen.queryByRole("menuitem", { name: "Уменьшить количество" }),
    ).not.toBeInTheDocument();
  });

  it.each(["payment", "completed"])("only offers copies when %s makes the sale read-only", () => {
    const { rerender } = render(<CartList {...props} quickActions={preferences()} />);
    open();
    rerender(<CartList {...props} editable={false} quickActions={preferences()} />);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    open();
    expect(screen.getByRole("menuitem", { name: "Скопировать название" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Скопировать номер партии" })).toBeInTheDocument();
    expect(screen.getAllByRole("menuitem")).toHaveLength(3);
    expect(screen.queryByRole("menuitem", { name: /количество|Удалить/ })).not.toBeInTheDocument();
    expect(onQtyChange).not.toHaveBeenCalled();
    expect(onDelete).not.toHaveBeenCalled();
  });

  it.each(["busy", "actionsDisabled"] as const)(
    "closes and disables all menus during %s",
    (flag) => {
      const { rerender } = render(<CartList {...props} quickActions={preferences()} />);
      open();
      rerender(<CartList {...props} {...{ [flag]: true }} quickActions={preferences()} />);
      expect(trigger()).toBeDisabled();
      expect(trigger()).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
      expect(fireEvent.contextMenu(screen.getByTestId("cart-item"))).toBe(true);
      fireEvent.click(trigger());
      expect(onQtyChange).not.toHaveBeenCalled();
      expect(onDelete).not.toHaveBeenCalled();
      rerender(<CartList {...props} quickActions={preferences()} />);
      expect(trigger()).toBeEnabled();
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
      open();
      fireEvent.click(screen.getByRole("menuitem", { name: "Удалить из чека" }));
      expect(onDelete).toHaveBeenCalledWith("item-a");
    },
  );

  it("does not offer to copy fallback IDs, missing names or blank batch numbers", () => {
    render(
      <CartList
        {...props}
        nameById={{}}
        items={[makeItem({ batch_number: " " })]}
        quickActions={preferences()}
      />,
    );
    open();
    expect(screen.queryByRole("menuitem", { name: /Скопировать/ })).not.toBeInTheDocument();
    expect(writeText).not.toHaveBeenCalled();
  });

  it("applies hidden commands and ordering while keeping danger last and a click fallback", () => {
    config = {
      enabled: false,
      order: ["remove", "copy_number", "decrease_qty", "increase_qty", "copy_name"],
      hidden: ["copy_name", "decrease_qty"],
    };
    render(<CartList {...props} touch quickActions={preferences()} />);
    const row = screen.getByTestId("cart-item");
    expect(row).toHaveAttribute("tabindex", "0");
    expect(fireEvent.contextMenu(row)).toBe(true);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger().parentElement).toHaveClass("[&>button[aria-haspopup=menu]]:h-14");
    open();
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Скопировать номер партии",
      "Увеличить количество",
      "Удалить из чека",
      "Настроить меню",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Настроить меню" }));
    expect(navigate).toHaveBeenCalledWith({
      to: "/settings",
      search: { section: "quick-actions", actions: "pos_cart" },
    });
  });

  it.each(["ContextMenu", "F10"])(
    "supports %s and restores focus to the batch row",
    async (key) => {
      render(<CartList {...props} quickActions={preferences()} />);
      const row = screen.getByTestId("cart-item");
      row.focus();
      expect(fireEvent.keyDown(row, { key, shiftKey: key === "F10" })).toBe(false);
      await waitFor(() =>
        expect(screen.getByRole("menuitem", { name: "Скопировать название" })).toHaveFocus(),
      );
      fireEvent.keyDown(document.activeElement!, { key: "Escape" });
      await waitFor(() => expect(row).toHaveFocus());
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    },
  );

  it("preserves native text selection and editable field context menus inside a row", () => {
    render(<CartList {...props} quickActions={preferences()} />);
    const row = screen.getByTestId("cart-item");
    for (const tag of ["input", "textarea", "select", "a"]) {
      const field = document.createElement(tag);
      row.appendChild(field);
      expect(fireEvent.contextMenu(field)).toBe(true);
      expect(fireEvent.keyDown(field, { key: "F10", shiftKey: true })).toBe(true);
      field.remove();
    }
    vi.spyOn(document.getSelection()!, "toString").mockReturnValue("Выделено");
    expect(fireEvent.contextMenu(row)).toBe(true);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it.each([
    { sale_id: "sale-b" },
    { id: "item-b" },
    { batch_id: "batch-b" },
    { qty: "3" },
    { unit_price: "11.00" },
    { total_price: "22.00" },
    { batch_number: "B-B" },
    { expires_at: "2028-01-01" },
  ])("dismisses a stale menu after record changes: %j", (change) => {
    const { rerender } = render(<CartList {...props} quickActions={preferences()} />);
    open();
    rerender(<CartList {...props} items={[makeItem(change)]} quickActions={preferences()} />);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(onQtyChange).not.toHaveBeenCalled();
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("dismisses stale copied values after a name change or an account switch", () => {
    const { rerender } = render(<CartList {...props} quickActions={preferences()} />);
    open();
    rerender(
      <CartList {...props} nameById={{ "catalog-a": "Новое имя" }} quickActions={preferences()} />,
    );
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    open();
    rerender(<CartList {...props} quickActions={{ config, scope: "another:user" }} />);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
