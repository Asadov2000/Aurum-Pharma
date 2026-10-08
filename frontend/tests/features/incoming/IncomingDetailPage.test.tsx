import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/features/catalog/fieldTemplates", () => ({
  useFieldTemplates: () => ({ data: [], isLoading: false, isError: false }),
  useCreateFieldTemplate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteFieldTemplate: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const getIncoming = vi.fn();
const acceptIncoming = vi.fn();
const rejectIncoming = vi.fn();
const deleteIncomingItem = vi.fn();
const listIncomingDraftRows = vi.fn();
const deleteIncomingDraftRow = vi.fn();
const saveIncomingDraftRow = vi.fn();
const previewIncomingInvoice = vi.fn();
const importIncomingInvoice = vi.fn();
const blockNavigation = vi.fn();
const resetNavigation = vi.fn();
const proceedNavigation = vi.fn();
let navigationBlocked = false;

let permissions = ["incoming.view", "incoming.create", "incoming.finalize"];
let permissionScopes: Record<string, string[] | null> = {};

vi.mock("@/features/incoming/api", () => ({
  getIncoming: (...args: unknown[]) => getIncoming(...args),
  acceptIncoming: (...args: unknown[]) => acceptIncoming(...args),
  rejectIncoming: (...args: unknown[]) => rejectIncoming(...args),
  deleteIncomingItem: (...args: unknown[]) => deleteIncomingItem(...args),
  listIncomingDraftRows: (...args: unknown[]) => listIncomingDraftRows(...args),
  deleteIncomingDraftRow: (...args: unknown[]) => deleteIncomingDraftRow(...args),
  saveIncomingDraftRow: (...args: unknown[]) => saveIncomingDraftRow(...args),
  previewIncomingInvoice: (...args: unknown[]) => previewIncomingInvoice(...args),
  importIncomingInvoice: (...args: unknown[]) => importIncomingInvoice(...args),
  commitIncomingDraftRow: vi.fn(),
  addIncomingItem: vi.fn(),
  updateIncomingItem: vi.fn(),
  createIncoming: vi.fn(),
  updateIncoming: vi.fn(),
  listIncoming: vi.fn(),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      id: "user-1",
      active_tenant_id: "tenant-1",
      is_developer: false,
      permissions,
      permission_scopes: permissionScopes,
    },
  }),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a href="/incoming">{children}</a>,
  useNavigate: () => vi.fn(),
  useParams: () => ({ id: "doc-1" }),
  useBlocker: (options: unknown) => {
    blockNavigation(options);
    return {
      status: navigationBlocked ? "blocked" : "idle",
      reset: resetNavigation,
      proceed: proceedNavigation,
    };
  },
}));

vi.mock("@/features/catalog/CatalogPicker", () => ({
  CatalogPicker: ({ id, initialLabel }: { id: string; initialLabel?: string }) => (
    <input id={id} readOnly value={initialLabel ?? ""} />
  ),
}));

import { useAuthStore } from "@/stores/auth";
import { type MeResponse } from "@/features/auth/types";

import { IncomingDetailPage } from "@/features/incoming/IncomingDetailPage";

const DOC = {
  id: "doc-1",
  tenant_id: "tenant-1",
  branch_id: "branch-1",
  branch_name: "Аптека Рудаки",
  supplier_id: "supplier-1",
  supplier_name: "Сино Фарм",
  document_number: "ПР-1042",
  document_date: "2026-08-01",
  status: "draft" as const,
  total_amount: "120.00",
  currency: "TJS",
  notes: "Проверить сертификат",
  document_file_path: null,
  created_at: "2026-08-01T08:00:00Z",
  updated_at: "2026-08-01T08:00:00Z",
  accepted_at: null,
  items: [
    {
      id: "item-1",
      document_id: "doc-1",
      catalog_id: "catalog-1",
      catalog_name: "Парацетамол 500 мг",
      catalog_form: "таблетки",
      catalog_dosage: "500 мг",
      catalog_pack_size: "20 шт",
      batch_number: "LOT-204",
      manufactured_at: "2026-01-10",
      expires_at: "2028-12-31",
      qty: "10",
      purchase_price: "12.00",
      sale_price: "15.00",
      currency: "TJS",
      created_batch_id: null,
    },
  ],
};

function renderPage(): QueryClient {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <IncomingDetailPage />
    </QueryClientProvider>,
  );
  return queryClient;
}

describe("IncomingDetailPage", () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.getState().setUser({ id: "user-1", active_tenant_id: "tenant-1" } as MeResponse);
    permissions = ["incoming.view", "incoming.create", "incoming.finalize"];
    permissionScopes = {
      "incoming.view": ["branch-1"],
      "incoming.create": ["branch-1"],
      "incoming.finalize": ["branch-1"],
    };
    getIncoming.mockReset();
    acceptIncoming.mockReset();
    rejectIncoming.mockReset();
    deleteIncomingItem.mockReset();
    listIncomingDraftRows.mockReset();
    listIncomingDraftRows.mockResolvedValue([]);
    deleteIncomingDraftRow.mockReset();
    saveIncomingDraftRow.mockReset();
    saveIncomingDraftRow.mockResolvedValue({ revision: 1 });
    previewIncomingInvoice.mockReset();
    importIncomingInvoice.mockReset();
    blockNavigation.mockClear();
    resetNavigation.mockClear();
    proceedNavigation.mockClear();
    navigationBlocked = false;
    getIncoming.mockResolvedValue(DOC);
  });

  it("shows reference names, product details and control totals", async () => {
    renderPage();

    expect(await screen.findByText("Приёмка № ПР-1042")).toBeInTheDocument();
    expect(screen.getByText(/Сино Фарм · Аптека Рудаки/)).toBeInTheDocument();
    expect(screen.getAllByText("Парацетамол 500 мг").length).toBeGreaterThanOrEqual(1);

    const summary = screen.getByRole("region", { name: "Сводка прихода" });
    expect(within(summary).getByText("120,00 TJS")).toBeInTheDocument();
    expect(within(summary).getByText("150,00 TJS")).toBeInTheDocument();
    expect(within(summary).getByText(/Наценка 30,00 TJS/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Добавить позицию" })).toBeInTheDocument();
  });

  it("keeps the accept confirmation open when the server rejects the operation", async () => {
    acceptIncoming.mockRejectedValue(new Error("conflict"));
    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Принять на склад" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад" }));
    const dialog = screen.getByRole("dialog", { name: "Принять товары на склад" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад" }));

    await waitFor(() => expect(acceptIncoming).toHaveBeenCalledWith("doc-1"));
    expect(
      await within(dialog).findByText(
        "Не удалось подтвердить приёмку. Проверьте статус перед повтором.",
      ),
    ).toBeInTheDocument();
    expect(dialog).toBeInTheDocument();
  });

  it("renders a retry action instead of an empty document after a load failure", async () => {
    getIncoming.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(DOC);
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить документ");
    expect(screen.queryByText("Документ пока пуст")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByText("Приёмка № ПР-1042")).toBeInTheDocument();
  });

  it("hides every modifying action from a read-only user", async () => {
    permissions = ["incoming.view"];
    permissionScopes = { "incoming.view": ["branch-1"] };
    renderPage();

    expect(await screen.findByText("Приёмка № ПР-1042")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Добавить позицию" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Изменить реквизиты" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Принять на склад" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отклонить" })).not.toBeInTheDocument();
  });

  it("hides modifying actions outside the permission branch scope", async () => {
    permissionScopes = {
      "incoming.view": ["branch-1"],
      "incoming.create": ["branch-2"],
      "incoming.finalize": ["branch-2"],
    };

    renderPage();

    expect(await screen.findByText("Приёмка № ПР-1042")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Добавить позицию" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Принять на склад" })).not.toBeInTheDocument();
  });

  it("lets a preparer edit the draft but not finalize it", async () => {
    permissions = ["incoming.view", "incoming.create"];
    renderPage();

    expect(await screen.findByRole("button", { name: "Добавить позицию" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Принять на склад" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отклонить" })).not.toBeInTheDocument();
  });

  it("previews mapped invoice columns before creating reviewable draft rows", async () => {
    previewIncomingInvoice.mockResolvedValue({
      headers: ["Наименование", "Количество", "Цена"],
      rows: [["Парацетамол", "4", "12,50"]],
      suggested_mapping: {
        source_name: 0,
        qty: 1,
        purchase_price: 2,
        sale_price: null,
        batch_number: null,
        expires_at: null,
        manufactured_at: null,
      },
      total_rows: 1,
      warnings: [],
    });
    importIncomingInvoice.mockResolvedValue({ imported_count: 1, existing_count: 0 });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Загрузить накладную" }));
    const dialog = screen.getByRole("dialog", { name: "Загрузить накладную" });
    const file = new File(["Наименование,Количество,Цена\nПарацетамол,4,12.50"], "invoice.csv", {
      type: "text/csv",
    });
    fireEvent.change(within(dialog).getByLabelText("Файл накладной"), {
      target: { files: [file] },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Проверить файл" }));
    await within(dialog).findByText("Найдено строк: 1. Ни одна строка ещё не добавлена.");
    expect(importIncomingInvoice).not.toHaveBeenCalled();
    expect(within(dialog).getByLabelText("Название товара *")).toHaveValue("0");
    fireEvent.click(within(dialog).getByRole("button", { name: "Добавить 1 строк в черновик" }));
    await waitFor(() => expect(importIncomingInvoice).toHaveBeenCalledOnce());
    expect(importIncomingInvoice.mock.calls[0]?.[0]).toBe("doc-1");
    expect(importIncomingInvoice.mock.calls[0]?.[1]).toBe(file);
    expect(importIncomingInvoice.mock.calls[0]?.[2]).toMatchObject({
      source_name: 0,
      qty: 1,
      purchase_price: 2,
    });
    expect(await screen.findByText(/1 строк добавлено в черновик/)).toBeInTheDocument();
  });

  it("does not import when source column is missing or columns are reused", async () => {
    previewIncomingInvoice.mockResolvedValue({
      headers: ["Товар", "Количество"],
      rows: [["Аспирин", "2"]],
      suggested_mapping: {
        source_name: null,
        qty: 1,
        purchase_price: null,
        sale_price: null,
        batch_number: null,
        expires_at: null,
        manufactured_at: null,
      },
      total_rows: 1,
      warnings: [],
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Загрузить накладную" }));
    const dialog = screen.getByRole("dialog", { name: "Загрузить накладную" });
    fireEvent.change(within(dialog).getByLabelText("Файл накладной"), {
      target: { files: [new File(["Товар,Количество\nАспирин,2"], "invoice.csv")] },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Проверить файл" }));
    await within(dialog).findByText(/Найдено строк: 1/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Добавить 1 строк в черновик" }));
    expect(
      await within(dialog).findByText("Укажите столбец с названием товара."),
    ).toBeInTheDocument();
    expect(importIncomingInvoice).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText("Название товара *"), {
      target: { value: "1" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Добавить 1 строк в черновик" }));
    expect(
      await within(dialog).findByText("Каждый столбец можно назначить только одному полю."),
    ).toBeInTheDocument();
    expect(importIncomingInvoice).not.toHaveBeenCalled();
  });

  it("retries an uncertain import with the same file and operation id", async () => {
    previewIncomingInvoice.mockResolvedValue({
      headers: ["Товар"],
      rows: [["Аспирин"]],
      suggested_mapping: {
        source_name: 0,
        qty: null,
        purchase_price: null,
        sale_price: null,
        batch_number: null,
        expires_at: null,
        manufactured_at: null,
      },
      total_rows: 1,
      warnings: [],
    });
    importIncomingInvoice
      .mockRejectedValueOnce(new Error("lost reply"))
      .mockResolvedValueOnce({ imported_count: 0, existing_count: 1 });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Загрузить накладную" }));
    const dialog = screen.getByRole("dialog", { name: "Загрузить накладную" });
    const file = new File(["Товар\nАспирин"], "invoice.csv");
    fireEvent.change(within(dialog).getByLabelText("Файл накладной"), {
      target: { files: [file] },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Проверить файл" }));
    await within(dialog).findByText(/Найдено строк: 1/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Добавить 1 строк в черновик" }));
    expect(await within(dialog).findByText(/Если ответ потерялся/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Файл накладной")).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Повторить загрузку" }));
    await waitFor(() => expect(importIncomingInvoice).toHaveBeenCalledTimes(2));
    expect(importIncomingInvoice.mock.calls[0]?.[1]).toBe(file);
    expect(importIncomingInvoice.mock.calls[1]?.[1]).toBe(file);
    expect(importIncomingInvoice.mock.calls[0]?.[3]).toBe(importIncomingInvoice.mock.calls[1]?.[3]);
    expect(await screen.findByText(/Дубли не созданы/)).toBeInTheDocument();
  });

  it("restores the raw pending row and blocks final acceptance", async () => {
    listIncomingDraftRows.mockResolvedValue([
      {
        id: "draft-1",
        document_id: DOC.id,
        revision: 2,
        catalog_name: "Парацетамол",
        fields: {
          catalog_id: "catalog-1",
          batch_number: "",
          manufactured_at: "",
          expires_at: "",
          qty: "12,",
          purchase_price: "",
          sale_price: "",
        },
      },
    ]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Продолжить" }));
    expect(screen.getByLabelText("Количество единиц")).toHaveValue("12,");
    expect(screen.getByLabelText("Лекарство или товар")).toHaveValue("Парацетамол");
    expect(screen.getByText("Сохранено в черновике")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Закрыть"));
    expect(screen.getByRole("button", { name: "Принять на склад" })).toBeDisabled();
    expect(acceptIncoming).not.toHaveBeenCalled();
  });

  it("never treats a failed pending-row lookup as an empty document ready for acceptance", async () => {
    listIncomingDraftRows.mockRejectedValue(new Error("offline"));
    renderPage();
    expect(await screen.findByText(/Не удалось проверить черновые позиции/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Принять на склад" })).toBeDisabled();
    expect(screen.queryByText("Незавершённых позиций нет")).not.toBeInTheDocument();
    listIncomingDraftRows.mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: "Повторить проверку" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Принять на склад" })).toBeEnabled(),
    );
  });

  it("guards close, reload and route navigation until the latest input is saved", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Добавить позицию" }));
    fireEvent.change(screen.getByLabelText("Количество единиц"), { target: { value: "12," } });
    await waitFor(() => expect(blockNavigation.mock.lastCall?.[0].enableBeforeUnload).toBe(true));
    expect(blockNavigation.mock.lastCall?.[0].shouldBlockFn()).toBe(true);
    fireEvent.click(screen.getByText("Закрыть"));
    const confirmation = screen.getByRole("dialog", { name: "Закрыть без сохранения?" });
    fireEvent.click(within(confirmation).getByRole("button", { name: "Отмена" }));
    expect(screen.getByLabelText("Количество единиц")).toHaveValue("12,");
    await screen.findByText("Сохранено в черновике");
    await waitFor(() => expect(blockNavigation.mock.lastCall?.[0].enableBeforeUnload).toBe(false));
    expect(blockNavigation.mock.lastCall?.[0].shouldBlockFn()).toBe(false);
  });

  it("preserves the open form when a background document refresh fails", async () => {
    const client = renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Добавить позицию" }));
    fireEvent.change(screen.getByLabelText("Количество единиц"), { target: { value: "12," } });
    getIncoming.mockRejectedValue(new Error("offline"));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["incoming", "doc", DOC.id] });
    });
    expect(await screen.findByText(/Не удалось обновить документ/)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Добавить позицию" })).toBeInTheDocument();
    expect(screen.getByLabelText("Количество единиц")).toHaveValue("12,");
  });

  it("places navigation confirmation above a portaled catalog form and handles Escape there", async () => {
    permissions.push("catalog.create");
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Добавить позицию" }));
    fireEvent.click(screen.getByRole("button", { name: "Новый товар" }));
    const catalog = await screen.findByRole("dialog", { name: "Добавить товар" });
    expect(screen.queryByRole("dialog", { name: "Уйти со страницы?" })).not.toBeInTheDocument();

    navigationBlocked = true;
    fireEvent.change(within(catalog).getByLabelText("Название лекарства"), {
      target: { value: "Несохранённый препарат" },
    });
    const confirmation = await screen.findByRole("dialog", { name: "Уйти со страницы?" });
    const dialogs = document.querySelectorAll("[data-aurum-modal]");
    expect(dialogs[dialogs.length - 1]).toBe(confirmation);
    expect(confirmation.contains(document.activeElement)).toBe(true);
    expect(blockNavigation.mock.lastCall?.[0].shouldBlockFn()).toBe(true);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(resetNavigation).toHaveBeenCalledOnce();
    expect(proceedNavigation).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("dialog", { name: "Закрыть карточку товара?" }),
    ).not.toBeInTheDocument();
    expect(within(catalog).getByLabelText("Название лекарства")).toHaveValue(
      "Несохранённый препарат",
    );
  });
  it("receives physical stock only after an explicit choice with the reviewed revision", async () => {
    const revision = "a".repeat(64);
    getIncoming.mockResolvedValue({
      ...DOC,
      physical_receiving_ready: true,
      acceptance_revision: revision,
    });
    acceptIncoming.mockResolvedValue({
      ...DOC,
      status: "accepted",
      stock_mode: "physical_unit_v2",
    });
    renderPage();
    await screen.findByText("Приёмка № ПР-1042");
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад" }));
    const dialog = screen.getByRole("dialog", { name: "Принять товары на склад" });
    expect(within(dialog).getByLabelText("Обычная приёмка упаковок")).toBeChecked();
    fireEvent.click(within(dialog).getByLabelText("Упаковки, блистеры и поштучно"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад" }));
    await waitFor(() =>
      expect(acceptIncoming).toHaveBeenCalledWith("doc-1", {
        stock_mode: "physical_unit_v2",
        expected_revision: revision,
      }),
    );
    await waitFor(() => expect(localStorage.length).toBe(0));
  });

  it("keeps the original physical revision after an unknown result and reload", async () => {
    const original = "b".repeat(64);
    getIncoming.mockResolvedValue({
      ...DOC,
      physical_receiving_ready: true,
      acceptance_revision: original,
    });
    acceptIncoming.mockRejectedValue(new Error("network interrupted"));
    renderPage();
    await screen.findByText("Приёмка № ПР-1042");
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад" }));
    let dialog = screen.getByRole("dialog", { name: "Принять товары на склад" });
    fireEvent.click(within(dialog).getByLabelText("Упаковки, блистеры и поштучно"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад" }));
    await within(dialog).findByText(
      "Не удалось подтвердить приёмку. Проверьте статус перед повтором.",
    );
    expect(screen.queryByRole("button", { name: "Изменить реквизиты" })).not.toBeInTheDocument();
    cleanup();
    getIncoming.mockResolvedValue({
      ...DOC,
      physical_receiving_ready: true,
      acceptance_revision: "c".repeat(64),
    });
    renderPage();
    await screen.findByRole("button", { name: "Проверить результат приёмки" });
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад" }));
    dialog = screen.getByRole("dialog", { name: "Принять товары на склад" });
    expect(within(dialog).getByLabelText("Обычная приёмка упаковок")).toBeDisabled();
    expect(within(dialog).getByLabelText("Упаковки, блистеры и поштучно")).toBeChecked();
    fireEvent.click(within(dialog).getByRole("button", { name: "Повторить тем же способом" }));
    await waitFor(() => expect(acceptIncoming).toHaveBeenCalledTimes(2));
    expect(acceptIncoming.mock.calls[1]).toEqual([
      "doc-1",
      { stock_mode: "physical_unit_v2", expected_revision: original },
    ]);
  });

  it("reconciles a confirmed physical receipt after reload without another write", async () => {
    const key = `aurum:incoming-accept:v1:${encodeURIComponent(JSON.stringify(["tenant-1", "user-1", "doc-1"]))}`;
    localStorage.setItem(
      key,
      JSON.stringify({ stock_mode: "physical_unit_v2", expected_revision: "d".repeat(64) }),
    );
    getIncoming.mockResolvedValue({ ...DOC, status: "accepted", stock_mode: "physical_unit_v2" });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Проверить результат приёмки" }));
    await waitFor(() => expect(localStorage.getItem(key)).toBeNull());
    expect(acceptIncoming).not.toHaveBeenCalled();
  });

  it("does not write after the account changes during the pending-row check", async () => {
    renderPage();
    await screen.findByText("Приёмка № ПР-1042");
    let resolveRows: (rows: []) => void = () => undefined;
    listIncomingDraftRows.mockImplementationOnce(
      () =>
        new Promise<[]>((resolve) => {
          resolveRows = resolve;
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад" }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Принять товары на склад" })).getByRole("button", {
        name: "Принять на склад",
      }),
    );
    await waitFor(() => expect(listIncomingDraftRows).toHaveBeenCalledTimes(2));
    await act(async () => {
      useAuthStore.getState().clear();
      resolveRows([]);
    });
    expect(acceptIncoming).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
  });

  it("disables physical receiving without a complete physical preparation", async () => {
    renderPage();
    await screen.findByText("Приёмка № ПР-1042");
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад" }));
    expect(screen.getByLabelText("Упаковки, блистеры и поштучно")).toBeDisabled();
  });
  it("does not clear the pending receipt after logout while a write response is late", async () => {
    let finish: (value: unknown) => void = () => undefined;
    acceptIncoming.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    renderPage();
    await screen.findByText("Приёмка № ПР-1042");
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад" }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Принять товары на склад" })).getByRole("button", {
        name: "Принять на склад",
      }),
    );
    await waitFor(() => expect(acceptIncoming).toHaveBeenCalledTimes(1));
    expect(localStorage.length).toBe(1);
    await act(async () => {
      useAuthStore.getState().clear();
      finish({ ...DOC, status: "accepted", stock_mode: "legacy_pack" });
    });
    expect(localStorage.length).toBe(1);
    expect(
      screen.queryByText("Товары приняты на склад. Документ теперь доступен только для просмотра."),
    ).not.toBeInTheDocument();
  });

  it("keeps a mismatched accepted mode blocked without another write", async () => {
    const key = `aurum:incoming-accept:v1:${encodeURIComponent(JSON.stringify(["tenant-1", "user-1", "doc-1"]))}`;
    localStorage.setItem(key, JSON.stringify({ stock_mode: "legacy_pack" }));
    getIncoming.mockResolvedValue({ ...DOC, status: "accepted", stock_mode: "prepared_unit_v1" });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Проверить результат приёмки" }));
    await screen.findByText("Документ принят другим способом. Обратитесь к администратору.");
    expect(localStorage.getItem(key)).not.toBeNull();
    expect(acceptIncoming).not.toHaveBeenCalled();
  });

  it("fails closed for a corrupt saved acceptance and checks the server without writing", async () => {
    const key = `aurum:incoming-accept:v1:${encodeURIComponent(JSON.stringify(["tenant-1", "user-1", "doc-1"]))}`;
    localStorage.setItem(key, "invalid saved value");
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Проверить документ" }));
    await screen.findByText(
      "Документ ещё не принят. Для проверки сохранённого способа обратитесь к администратору.",
    );
    expect(screen.queryByRole("button", { name: "Изменить реквизиты" })).not.toBeInTheDocument();
    expect(acceptIncoming).not.toHaveBeenCalled();
  });
  it("refreshes the reviewed revision after a definite rejection and a confirmed draft", async () => {
    const original = "e".repeat(64);
    const fresh = "f".repeat(64);
    getIncoming.mockResolvedValue({
      ...DOC,
      physical_receiving_ready: true,
      acceptance_revision: original,
    });
    acceptIncoming
      .mockImplementationOnce(async () => {
        getIncoming.mockResolvedValue({
          ...DOC,
          physical_receiving_ready: true,
          acceptance_revision: fresh,
        });
        throw Object.assign(new Error("Document changed"), {
          isAxiosError: true,
          response: { status: 409, data: {} },
        });
      })
      .mockResolvedValue({ ...DOC, status: "accepted", stock_mode: "physical_unit_v2" });
    renderPage();
    await screen.findByText("Приёмка № ПР-1042");
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад" }));
    const dialog = screen.getByRole("dialog", { name: "Принять товары на склад" });
    fireEvent.click(within(dialog).getByLabelText("Упаковки, блистеры и поштучно"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад" }));
    await waitFor(() => expect(acceptIncoming).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(within(dialog).getByLabelText("Упаковки, блистеры и поштучно")).toBeEnabled(),
    );
    expect(localStorage.length).toBe(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад" }));
    await waitFor(() => expect(acceptIncoming).toHaveBeenCalledTimes(2));
    expect(acceptIncoming.mock.calls[0]).toEqual([
      "doc-1",
      { stock_mode: "physical_unit_v2", expected_revision: original },
    ]);
    expect(acceptIncoming.mock.calls[1]).toEqual([
      "doc-1",
      { stock_mode: "physical_unit_v2", expected_revision: fresh },
    ]);
  });
});
