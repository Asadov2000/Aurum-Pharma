import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listSales = vi.fn();
const getSaleDetails = vi.fn();
const refundSale = vi.fn();
const navigate = vi.fn();
const writeText = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => navigate,
}));

vi.mock("@/features/settings/useQuickActionPreferences", () => ({
  useQuickActionPreferences: () => ({ config: quickActionConfig, scope: "user:tenant" }),
}));

vi.mock("@/features/sales/api", () => ({
  listSales: (...a: unknown[]) => listSales(...a),
  getSaleDetails: (...a: unknown[]) => getSaleDetails(...a),
  refundSale: (...a: unknown[]) => refundSale(...a),
}));

vi.mock("@/features/foundation/api", () => ({
  listBranches: vi.fn().mockResolvedValue([]),
  listRegisters: vi.fn().mockResolvedValue([]),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: { home_tenant_id: "t-1", is_developer: false } }),
}));

import { SalesPage } from "@/features/sales/SalesPage";
import {
  defaultQuickActionPreferences,
  type QuickActionMenuPreferences,
} from "@/features/settings/quickActions";

let quickActionConfig: QuickActionMenuPreferences;

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <SalesPage />
    </QueryClientProvider>,
  );
  return { ...view, qc };
}

const SALE = {
  id: "s-1",
  receipt_number: "000142",
  completed_at: "2026-05-28T10:00:00Z",
  branch_name: "Аптека №1",
  register_name: "Касса 1",
  cashier_name: "Иван Кассиров",
  total_amount: "120.00",
  currency: "TJS",
  payment_methods: ["cash"],
  is_refund: false,
  parent_sale_id: null,
  parent_receipt_number: null,
  has_refund: true,
  refund_receipt_number: "000143",
  items_summary: "Парацетамол x2",
  status: "completed",
};

describe("SalesPage", () => {
  beforeEach(() => {
    listSales.mockReset();
    getSaleDetails.mockReset();
    refundSale.mockReset();
    navigate.mockReset();
    writeText.mockReset();
    writeText.mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    quickActionConfig = defaultQuickActionPreferences("receipts");
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders an empty state when there are no receipts", async () => {
    listSales.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 50 });
    renderPage();
    expect(await screen.findByText(/Чеков пока нет/i)).toBeInTheDocument();
  });

  it("renders a receipt row with resolved cashier name and refund badge", async () => {
    listSales.mockResolvedValueOnce({ items: [SALE], total: 1, page: 1, page_size: 50 });
    renderPage();
    const receipt = await screen.findByRole("button", { name: "Открыть чек № 000142" });
    expect(within(receipt).getAllByText("Иван Кассиров").length).toBeGreaterThan(0);
    expect(within(receipt).getAllByText("Наличные").length).toBeGreaterThan(0);
    expect(within(receipt).getAllByText("Возвращено частично").length).toBeGreaterThan(0);
    expect(within(receipt).getAllByText("Чек №000143").length).toBeGreaterThan(0);
    expect(screen.getByText("Найдено: 1")).toBeInTheDocument();
  });

  it("applies the verified has_refund quick view without inventing other counters", async () => {
    listSales.mockResolvedValue({ items: [SALE], total: 1, page: 1, page_size: 50 });
    renderPage();

    await screen.findByRole("button", { name: "Открыть чек № 000142" });
    fireEvent.click(screen.getByRole("button", { name: "Продажи с возвратом" }));

    await waitFor(() => {
      expect(listSales).toHaveBeenLastCalledWith(expect.objectContaining({ has_refund: true }));
    });
    expect(screen.getByRole("button", { name: /Продажи с возвратом/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    fireEvent.click(screen.getByRole("button", { name: "Чеки возврата" }));
    await waitFor(() => {
      expect(listSales).toHaveBeenLastCalledWith(
        expect.objectContaining({ sale_type: "return", has_refund: undefined }),
      );
    });
    expect(screen.getByRole("button", { name: /Чеки возврата/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("re-queries with the receipt_number filter typed in", async () => {
    listSales.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
    renderPage();
    await screen.findByText(/Чеков пока нет/i);
    fireEvent.change(screen.getByLabelText("№ чека"), { target: { value: "000142" } });
    await waitFor(() => {
      expect(listSales).toHaveBeenLastCalledWith(
        expect.objectContaining({ receipt_number: "000142", page: 1 }),
      );
    });
  });

  it("opens the detail modal on row click and loads full receipt", async () => {
    listSales.mockResolvedValueOnce({ items: [SALE], total: 1, page: 1, page_size: 50 });
    getSaleDetails.mockResolvedValueOnce({
      id: "s-1",
      sale_type: "sale",
      status: "completed",
      receipt_number: "000142",
      completed_at: "2026-05-28T10:00:00Z",
      total_amount: "120.00",
      currency: "TJS",
      parent_sale_id: null,
      items: [
        {
          id: "i-1",
          sale_id: "s-1",
          catalog_id: "cat-1",
          batch_id: "b-1",
          qty: "2",
          unit_price: "60.00",
          total_price: "120.00",
          currency: "TJS",
          discount_amount: "0",
          position: 1,
          name: "Парацетамол 500 мг",
        },
      ],
      payments: [
        { id: "p-1", sale_id: "s-1", payment_method: "cash", amount: "120.00", currency: "TJS" },
      ],
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Открыть чек № 000142" }));
    expect(await screen.findByText(/Чек № 000142/)).toBeInTheDocument();
    await waitFor(() => {
      expect(getSaleDetails).toHaveBeenCalledWith("s-1");
    });
    expect(screen.getByText("Парацетамол 500 мг")).toBeInTheDocument();
    // Refund button hidden because this row already has_refund.
    expect(screen.queryByRole("button", { name: /Оформить возврат/ })).not.toBeInTheDocument();
  });

  it("offers a retry when receipt details fail to load", async () => {
    listSales.mockResolvedValueOnce({ items: [SALE], total: 1, page: 1, page_size: 50 });
    getSaleDetails
      .mockRejectedValueOnce(new Error("temporary network error"))
      .mockResolvedValueOnce({
        id: "s-1",
        sale_type: "sale",
        status: "completed",
        receipt_number: "000142",
        completed_at: "2026-05-28T10:00:00Z",
        total_amount: "120.00",
        currency: "TJS",
        parent_sale_id: null,
        items: [],
        payments: [],
      });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Открыть чек № 000142" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить чек");
    expect(screen.queryByText("Загрузка…")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByText("Продажа завершена")).toBeInTheDocument();
    expect(getSaleDetails).toHaveBeenCalledTimes(2);
  });

  it("opens the right-clicked receipt through the existing detail modal", async () => {
    listSales.mockResolvedValue({
      items: [SALE, { ...SALE, id: "s-2", receipt_number: "000144", is_refund: true }],
      total: 2,
      page: 1,
      page_size: 50,
    });
    getSaleDetails.mockResolvedValue({
      ...SALE,
      id: "s-2",
      receipt_number: "000144",
      sale_type: "return",
      items: [],
      payments: [],
    });
    renderPage();
    const button = await screen.findByRole("button", { name: "Открыть чек № 000144" });
    const row = button.closest("article")!;
    expect(button.querySelector("button")).toBeNull();
    fireEvent.contextMenu(row);
    const menu = screen.getByRole("menu");
    expect(within(menu).getByText("Чек возврата № 000144")).toBeInTheDocument();
    expect(
      within(menu)
        .getAllByRole("menuitem")
        .map((item) => item.textContent),
    ).toEqual(["Открыть чек", "Скопировать номер чека", "Настроить это меню"]);
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Открыть чек" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await waitFor(() => expect(getSaleDetails).toHaveBeenCalledWith("s-2"));
    expect(refundSale).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(row).toHaveFocus();
  });

  it("copies only the actual receipt number without loading or refunding a receipt", async () => {
    listSales.mockResolvedValue({ items: [SALE], total: 1, page: 1, page_size: 50 });
    renderPage();
    const button = await screen.findByRole("button", { name: "Открыть чек № 000142" });
    fireEvent.contextMenu(button);
    fireEvent.click(screen.getByRole("menuitem", { name: "Скопировать номер чека" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("000142"));
    expect(await screen.findByRole("status")).toHaveTextContent("Скопировано");
    expect(getSaleDetails).not.toHaveBeenCalled();
    expect(refundSale).not.toHaveBeenCalled();
  });

  it.each([{ key: "F10", shiftKey: true }, { key: "ContextMenu" }])(
    "opens the receipt menu with $key",
    async (key) => {
      listSales.mockResolvedValue({ items: [SALE], total: 1, page: 1, page_size: 50 });
      renderPage();
      const button = await screen.findByRole("button", { name: "Открыть чек № 000142" });
      button.focus();
      fireEvent.keyDown(button, key);
      expect(screen.getByRole("menuitem", { name: "Открыть чек" })).toBeInTheDocument();
      expect(getSaleDetails).not.toHaveBeenCalled();
      expect(refundSale).not.toHaveBeenCalled();
    },
  );

  it("keeps the sibling menu button and settings route when right-click is disabled", async () => {
    quickActionConfig = { ...quickActionConfig, enabled: false };
    listSales.mockResolvedValue({ items: [SALE], total: 1, page: 1, page_size: 50 });
    renderPage();
    const button = await screen.findByRole("button", { name: "Открыть чек № 000142" });
    const row = button.closest("article")!;
    expect(fireEvent.contextMenu(row)).toBe(true);
    fireEvent.keyDown(row, { key: "F10", shiftKey: true });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    const trigger = within(row).getByRole("button", { name: "Действия для чека № 000142" });
    expect(button.contains(trigger)).toBe(false);
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("menuitem", { name: "Настроить это меню" }));
    expect(navigate).toHaveBeenCalledWith({
      to: "/settings",
      search: { section: "quick-actions", actions: "receipts" },
    });
    expect(getSaleDetails).not.toHaveBeenCalled();
    expect(refundSale).not.toHaveBeenCalled();
  });

  it.each([null, "", "   "])("omits copying a missing receipt number (%s)", async (number) => {
    listSales.mockResolvedValue({
      items: [{ ...SALE, receipt_number: number }],
      total: 1,
      page: 1,
      page_size: 50,
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Действия для чека без номера" }));
    expect(screen.getByRole("menuitem", { name: "Открыть чек" })).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: "Скопировать номер чека" }),
    ).not.toBeInTheDocument();
    expect(writeText).not.toHaveBeenCalled();
    expect(refundSale).not.toHaveBeenCalled();
  });

  it("closes and disables receipt actions while previous-page data is a placeholder", async () => {
    listSales
      .mockResolvedValueOnce({ items: [SALE], total: 51, page: 1, page_size: 50 })
      .mockReturnValue(new Promise(() => undefined));
    renderPage();
    const button = await screen.findByRole("button", { name: "Открыть чек № 000142" });
    fireEvent.contextMenu(button);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Вперёд/ }));
    await waitFor(() => expect(button).toBeDisabled());
    expect(screen.getByRole("button", { name: "Действия для чека № 000142" })).toBeDisabled();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(fireEvent.contextMenu(button.closest("article")!)).toBe(true);
    fireEvent.click(button);
    expect(getSaleDetails).not.toHaveBeenCalled();
    expect(refundSale).not.toHaveBeenCalled();
  });

  it.each([
    { status: "voided" },
    { has_refund: false },
    { is_refund: true },
    { receipt_number: "000145" },
  ])("closes the menu when receipt data changes (%j)", async (change) => {
    listSales.mockResolvedValue({ items: [SALE], total: 1, page: 1, page_size: 50 });
    const { qc } = renderPage();
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Открыть чек № 000142" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    act(() => {
      qc.setQueriesData(
        { queryKey: ["sales", "list"] },
        { items: [{ ...SALE, ...change }], total: 1, page: 1, page_size: 50 },
      );
    });
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
    expect(getSaleDetails).not.toHaveBeenCalled();
    expect(refundSale).not.toHaveBeenCalled();
  });

  it("blocks cached receipt actions after a failed list refetch", async () => {
    listSales
      .mockResolvedValueOnce({ items: [SALE], total: 1, page: 1, page_size: 50 })
      .mockRejectedValue(new Error("network error"));
    const { qc } = renderPage();
    const button = await screen.findByRole("button", { name: "Открыть чек № 000142" });
    fireEvent.contextMenu(button);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await qc.invalidateQueries({ queryKey: ["sales", "list"] });
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить чеки");
    expect(button).toBeDisabled();
    const trigger = screen.getByRole("button", { name: "Действия для чека № 000142" });
    expect(trigger).toBeDisabled();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.contextMenu(button.closest("article")!);
    fireEvent.click(trigger);
    fireEvent.click(button);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(getSaleDetails).not.toHaveBeenCalled();
    expect(refundSale).not.toHaveBeenCalled();
  });
});
