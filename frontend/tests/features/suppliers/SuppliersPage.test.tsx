import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const searchSuppliers = vi.fn();
const createSupplier = vi.fn();
const searchSupplierReturns = vi.fn();
const searchSupplierReturnCandidates = vi.fn();
const createSupplierReturn = vi.fn();

vi.mock("@/features/suppliers/api", () => ({
  listSuppliers: vi.fn(),
  searchSuppliers: (...a: unknown[]) => searchSuppliers(...a),
  searchSupplierOptions: vi.fn(),
  createSupplier: (...a: unknown[]) => createSupplier(...a),
  updateSupplier: vi.fn(),
  searchSupplierReturns: (...a: unknown[]) => searchSupplierReturns(...a),
  searchSupplierReturnCandidates: (...a: unknown[]) => searchSupplierReturnCandidates(...a),
  createSupplierReturn: (...a: unknown[]) => createSupplierReturn(...a),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      home_tenant_id: "t-1",
      is_developer: false,
      permissions: [
        "suppliers.view",
        "suppliers.create",
        "suppliers.update",
        "incoming.view",
        "incoming.return",
      ],
      permission_scopes: {
        "suppliers.view": null,
        "suppliers.create": null,
        "suppliers.update": null,
        "incoming.view": null,
        "incoming.return": null,
      },
    },
  }),
}));

import { SuppliersPage } from "@/features/suppliers/SuppliersPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SuppliersPage />
    </QueryClientProvider>,
  );
}

function clickContactLink(link: HTMLElement) {
  // Cancel browser navigation only after the React row/card handler has run.
  window.addEventListener("click", (event) => event.preventDefault(), { once: true });
  fireEvent.click(link);
}

const SAMPLE = {
  id: "s-1",
  tenant_id: "t-1",
  name: "ОсОО Прима-Фарм",
  legal_name: null,
  inn_or_tin: null,
  contact_person: "Иван Иванов",
  phone: "+992 900 12 34 56",
  email: "prima@example.tj",
  address: null,
  notes: null,
  is_active: true,
  created_at: "2026-05-23T00:00:00Z",
  updated_at: "2026-05-23T00:00:00Z",
};

const SUMMARY = {
  all_count: 1,
  active_count: 1,
  inactive_count: 0,
  with_contact_count: 1,
};

describe("SuppliersPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    searchSuppliers.mockReset();
    createSupplier.mockReset();
    searchSupplierReturns.mockReset();
    searchSupplierReturnCandidates.mockReset();
    createSupplierReturn.mockReset();
    searchSupplierReturns.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 10,
      summary: { total_qty: "0", total_amount: "0" },
    });
    searchSupplierReturnCandidates.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the empty state when no suppliers exist", async () => {
    searchSuppliers.mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, all_count: 0, active_count: 0, with_contact_count: 0 },
    });
    renderPage();
    expect(await screen.findByText(/Поставщиков пока нет/i)).toBeInTheDocument();
  });

  it("renders rows returned from the API", async () => {
    searchSuppliers.mockResolvedValueOnce({
      items: [SAMPLE],
      total: 1,
      page: 1,
      page_size: 25,
      summary: SUMMARY,
    });
    renderPage();
    expect(await screen.findByText("ОсОО Прима-Фарм")).toBeInTheDocument();
    expect(screen.getByText("Иван Иванов")).toBeInTheDocument();
    expect(screen.getByText("prima@example.tj")).toBeInTheDocument();
  });

  it("creates a supplier with empty optionals serialized as null", async () => {
    searchSuppliers.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, all_count: 0, active_count: 0, with_contact_count: 0 },
    });
    createSupplier.mockResolvedValueOnce(SAMPLE);
    renderPage();
    await screen.findByText(/Поставщиков пока нет/i);
    fireEvent.click(screen.getAllByRole("button", { name: "Добавить поставщика" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "Добавить поставщика" });
    fireEvent.change(within(dialog).getByLabelText("Краткое название"), {
      target: { value: "ОсОО Прима-Фарм" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Добавить поставщика" }));
    await waitFor(() => {
      expect(createSupplier).toHaveBeenCalledTimes(1);
    });
    // Empty optional fields land as null, not "" or undefined — matches
    // the FastAPI schema where Optional[str] = None is expected.
    expect(createSupplier).toHaveBeenCalledWith(
      expect.objectContaining({
        operation_id: expect.stringMatching(
          /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
        ),
        name: "ОсОО Прима-Фарм",
        legal_name: null,
        inn_or_tin: null,
        contact_person: null,
        phone: null,
        email: null,
        address: null,
        notes: null,
      }),
    );
  });

  it("warns before discarding an unfinished supplier", async () => {
    searchSuppliers.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, all_count: 0, active_count: 0, with_contact_count: 0 },
    });
    renderPage();
    await screen.findByText(/Поставщиков пока нет/i);
    fireEvent.click(screen.getAllByRole("button", { name: "Добавить поставщика" })[0]);
    const editor = await screen.findByRole("dialog", { name: "Добавить поставщика" });
    fireEvent.change(within(editor).getByLabelText("Краткое название"), {
      target: { value: "Черновик" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: "Отмена" }));

    expect(
      await screen.findByRole("dialog", { name: "Закрыть без сохранения?" }),
    ).toBeInTheDocument();
    expect(editor).toBeInTheDocument();
  });

  it("debounces search and applies an explicit inactive status", async () => {
    searchSuppliers.mockResolvedValue({
      items: [SAMPLE],
      total: 1,
      page: 1,
      page_size: 25,
      summary: SUMMARY,
    });
    renderPage();
    await screen.findByText("ОсОО Прима-Фарм");

    fireEvent.change(screen.getByLabelText("Поиск"), {
      target: { value: "  Прима  " },
    });
    fireEvent.change(screen.getByLabelText("Статус"), {
      target: { value: "inactive" },
    });

    await waitFor(() =>
      expect(searchSuppliers).toHaveBeenCalledWith(
        expect.objectContaining({
          q: "Прима",
          is_active: false,
          page: 1,
          page_size: 25,
        }),
        expect.any(AbortSignal),
      ),
    );
  });

  it("shows the selected supplier beside the table on a wide screen", async () => {
    const second = {
      ...SAMPLE,
      id: "s-2",
      name: "ООО Фарма Сино",
      contact_person: "Мехродж Саидов",
    };
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches: query.includes("768px") || query.includes("1280px"),
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
    searchSuppliers.mockResolvedValueOnce({
      items: [SAMPLE, second],
      total: 2,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, all_count: 2, active_count: 2, with_contact_count: 2 },
    });
    renderPage();

    expect(
      await screen.findByRole("region", { name: `Карточка поставщика: ${SAMPLE.name}` }),
    ).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Поставщики" });
    fireEvent.click(within(table).getByText(second.contact_person));
    expect(
      screen.getByRole("region", { name: `Карточка поставщика: ${second.name}` }),
    ).toBeInTheDocument();
    expect(searchSupplierReturns).toHaveBeenCalledWith(
      expect.objectContaining({ supplier_id: second.id, page: 1, page_size: 3 }),
      expect.any(AbortSignal),
    );
    const firstRow = within(table).getByRole("button", { name: SAMPLE.name }).closest("tr")!;
    clickContactLink(within(firstRow).getByRole("link", { name: SAMPLE.phone }));
    clickContactLink(within(firstRow).getByRole("link", { name: SAMPLE.email }));
    expect(
      screen.getByRole("region", { name: `Карточка поставщика: ${second.name}` }),
    ).toBeInTheDocument();
    expect(firstRow).not.toHaveAttribute("tabindex");
    expect(firstRow).not.toHaveAttribute("role", "button");
    const open = within(firstRow).getByRole("button", { name: `Открыть карточку: ${SAMPLE.name}` });
    open.focus();
    expect(open).toHaveFocus();
    fireEvent.click(open);
    expect(
      screen.getByRole("region", { name: `Карточка поставщика: ${SAMPLE.name}` }),
    ).toBeInTheDocument();
  });

  it("opens a card from its free surface but leaves its contact links independent", async () => {
    searchSuppliers.mockResolvedValueOnce({
      items: [SAMPLE],
      total: 1,
      page: 1,
      page_size: 25,
      summary: SUMMARY,
    });
    renderPage();
    const card = (await screen.findByText(SAMPLE.name)).closest("article")!;
    clickContactLink(within(card).getByRole("link", { name: SAMPLE.phone }));
    clickContactLink(within(card).getByRole("link", { name: SAMPLE.email }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(within(card).getByText(SAMPLE.contact_person));
    expect(await screen.findByRole("dialog", { name: SAMPLE.name })).toBeInTheDocument();
  });

  it("does not open previous search results while a replacement request is pending", async () => {
    searchSuppliers.mockResolvedValueOnce({
      items: [SAMPLE],
      total: 1,
      page: 1,
      page_size: 25,
      summary: SUMMARY,
    });
    searchSuppliers.mockImplementation(() => new Promise(() => undefined));
    renderPage();
    const card = (await screen.findByText(SAMPLE.name)).closest("article")!;
    fireEvent.change(screen.getByPlaceholderText("Название, контакт, телефон или ИНН"), {
      target: { value: "другой поставщик" },
    });
    await waitFor(() =>
      expect(within(card).getByRole("button", { name: "Открыть карточку" })).toBeDisabled(),
    );
    fireEvent.click(within(card).getByText(SAMPLE.contact_person));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
