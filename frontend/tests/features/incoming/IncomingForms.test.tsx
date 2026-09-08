import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AxiosError } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

const saveIncomingDraftRow = vi.fn();
const commitIncomingDraftRow = vi.fn();
const listIncomingDraftRows = vi.fn();
const updateIncomingItem = vi.fn();
const updateIncoming = vi.fn();
const listBranches = vi.fn();
const searchSupplierOptions = vi.fn();
const navigate = vi.fn();

vi.mock("@/features/incoming/api", () => ({
  saveIncomingDraftRow: (...args: unknown[]) => saveIncomingDraftRow(...args),
  commitIncomingDraftRow: (...args: unknown[]) => commitIncomingDraftRow(...args),
  listIncomingDraftRows: (...args: unknown[]) => listIncomingDraftRows(...args),
  updateIncomingItem: (...args: unknown[]) => updateIncomingItem(...args),
  getIncoming: vi.fn(),
  acceptIncoming: vi.fn(),
  rejectIncoming: vi.fn(),
  deleteIncomingItem: vi.fn(),
  createIncoming: vi.fn(),
  updateIncoming: (...args: unknown[]) => updateIncoming(...args),
  listIncoming: vi.fn(),
}));

vi.mock("@/features/foundation/api", () => ({
  listBranches: (...args: unknown[]) => listBranches(...args),
  listTenants: vi.fn(),
  createTenant: vi.fn(),
  updateTenant: vi.fn(),
  getTenantSettings: vi.fn(),
  updateTenantSettings: vi.fn(),
  createBranch: vi.fn(),
  updateBranch: vi.fn(),
  deleteBranch: vi.fn(),
  listRegisters: vi.fn(),
  createRegister: vi.fn(),
  updateRegister: vi.fn(),
  deleteRegister: vi.fn(),
}));

vi.mock("@/features/suppliers/api", () => ({
  listSuppliers: vi.fn(),
  searchSuppliers: vi.fn(),
  searchSupplierOptions: (...args: unknown[]) => searchSupplierOptions(...args),
  createSupplier: vi.fn(),
  updateSupplier: vi.fn(),
  searchSupplierReturns: vi.fn(),
  searchSupplierReturnCandidates: vi.fn(),
  createSupplierReturn: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}));

vi.mock("@/features/catalog/CatalogPicker", () => ({
  CatalogPicker: ({
    id,
    onChange,
    initialLabel,
  }: {
    id?: string;
    onChange: (id: string, name: string) => void;
    initialLabel?: string;
  }) => (
    <button id={id} type="button" onClick={() => onChange("catalog-1", "Парацетамол")}>
      {initialLabel ?? "Выбрать товар"}
    </button>
  ),
}));

import { AddItemForm } from "@/features/incoming/AddItemForm";
import { NewIncomingForm } from "@/features/incoming/NewIncomingForm";

const BRANCH = {
  id: "branch-1",
  tenant_id: "tenant-1",
  name: "Аптека Рудаки",
  address: null,
  branch_type: "pharmacy" as const,
  license_number: null,
  license_expires_at: null,
  working_hours: null,
  receipt_header: null,
  is_active: true,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const SUPPLIER = {
  id: "supplier-1",
  tenant_id: "tenant-1",
  name: "Сино Фарм",
  legal_name: null,
  inn_or_tin: null,
  contact_person: null,
  phone: null,
  email: null,
  address: null,
  notes: null,
  is_active: true,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

function renderForm(
  onClose = vi.fn(),
  item?: React.ComponentProps<typeof AddItemForm>["item"],
): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <AddItemForm documentId="doc-1" item={item} onClose={onClose} />
    </QueryClientProvider>,
  );
}

describe("AddItemForm", () => {
  beforeEach(() => {
    saveIncomingDraftRow.mockReset();
    commitIncomingDraftRow.mockReset();
    listIncomingDraftRows.mockReset();
    updateIncomingItem.mockReset();
    saveIncomingDraftRow.mockResolvedValue({ revision: 1 });
    commitIncomingDraftRow.mockResolvedValue({ id: "item-1" });
    updateIncomingItem.mockResolvedValue({ id: "item-1" });
  });

  it("rejects empty prices before sending a request", async () => {
    renderForm();
    fireEvent.click(screen.getByRole("button", { name: "Лекарство или товар" }));
    fireEvent.change(screen.getByLabelText("Срок годности"), {
      target: { value: "2099-12-31" },
    });
    fireEvent.change(screen.getByLabelText("Количество единиц"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить", exact: true }));

    expect(await screen.findByText("Укажите цену закупки")).toBeInTheDocument();
    expect(screen.getByText("Укажите цену продажи")).toBeInTheDocument();
    expect(commitIncomingDraftRow).not.toHaveBeenCalled();
  });

  it("saves raw decimal commas before committing the confirmed revision", async () => {
    const onClose = vi.fn();
    renderForm(onClose);
    fireEvent.click(screen.getByRole("button", { name: "Лекарство или товар" }));
    fireEvent.change(screen.getByLabelText("Срок годности"), {
      target: { value: "2099-12-31" },
    });
    fireEvent.change(screen.getByLabelText("Количество единиц"), { target: { value: "2,5" } });
    fireEvent.change(screen.getByLabelText("Закупочная цена за единицу"), {
      target: { value: "4,50" },
    });
    fireEvent.change(screen.getByLabelText("Розничная цена за единицу"), {
      target: { value: "5,25" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Добавить", exact: true }));

    await waitFor(() =>
      expect(saveIncomingDraftRow).toHaveBeenCalledWith(
        "doc-1",
        expect.any(String),
        expect.objectContaining({
          expected_revision: 0,
          fields: expect.objectContaining({
            catalog_id: "catalog-1",
            qty: "2,5",
            purchase_price: "4,50",
            sale_price: "5,25",
          }),
        }),
      ),
    );
    expect(commitIncomingDraftRow).toHaveBeenCalledWith(
      "doc-1",
      saveIncomingDraftRow.mock.calls[0]?.[1],
      1,
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("validates the manufacturing date against the expiry date", async () => {
    renderForm();
    fireEvent.click(screen.getByRole("button", { name: "Лекарство или товар" }));
    fireEvent.change(screen.getByLabelText("Дата производства"), {
      target: { value: "2099-12-31" },
    });
    fireEvent.change(screen.getByLabelText("Срок годности"), {
      target: { value: "2099-01-01" },
    });
    fireEvent.change(screen.getByLabelText("Количество единиц"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Закупочная цена за единицу"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByLabelText("Розничная цена за единицу"), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Добавить", exact: true }));

    expect(
      await screen.findByText("Дата производства не может быть позже срока годности"),
    ).toBeInTheDocument();
    expect(commitIncomingDraftRow).not.toHaveBeenCalled();
  });

  it("rejects values that exceed database precision", async () => {
    renderForm();
    fireEvent.click(screen.getByRole("button", { name: "Лекарство или товар" }));
    fireEvent.change(screen.getByLabelText("Срок годности"), {
      target: { value: "2099-12-31" },
    });
    fireEvent.change(screen.getByLabelText("Количество единиц"), {
      target: { value: "0.0001" },
    });
    fireEvent.change(screen.getByLabelText("Закупочная цена за единицу"), {
      target: { value: "1.999" },
    });
    fireEvent.change(screen.getByLabelText("Розничная цена за единицу"), {
      target: { value: "1234567890123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Добавить", exact: true }));

    expect(
      await screen.findByText("Количество: до 11 цифр и 3 знаков после запятой, больше 0"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Цена: до 12 цифр и 2 знаков после запятой")).toHaveLength(2);
    expect(commitIncomingDraftRow).not.toHaveBeenCalled();
  });

  it("updates an item and explicitly clears nullable fields", async () => {
    const onClose = vi.fn();
    renderForm(onClose, {
      id: "item-1",
      document_id: "doc-1",
      catalog_id: "catalog-1",
      batch_number: "BATCH-1",
      manufactured_at: "2026-01-01",
      expires_at: "2099-12-31",
      qty: "1.000",
      purchase_price: "4.00",
      sale_price: "5.00",
      currency: "TJS",
      created_batch_id: null,
      catalog_name: "Парацетамол",
    });

    fireEvent.change(screen.getByLabelText("Номер партии"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Дата производства"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Розничная цена за единицу"), {
      target: { value: "6.00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(updateIncomingItem).toHaveBeenCalledWith(
        "doc-1",
        "item-1",
        expect.objectContaining({
          batch_number: null,
          manufactured_at: null,
          sale_price: "6.00",
        }),
      ),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("keeps newer RHF input when an earlier autosave finishes", async () => {
    let resolveFirst!: (value: { revision: number }) => void;
    saveIncomingDraftRow
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockResolvedValue({ revision: 2 });
    renderForm();
    fireEvent.change(screen.getByLabelText("Количество единиц"), { target: { value: "12," } });
    await waitFor(() => expect(saveIncomingDraftRow).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText("Количество единиц"), { target: { value: "13," } });
    await act(async () => {
      resolveFirst({ revision: 1 });
    });
    await waitFor(() => expect(saveIncomingDraftRow).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText("Количество единиц")).toHaveValue("13,");
    expect(screen.getByText("Сохранено в черновике")).toBeInTheDocument();
  });

  it("preserves conflicting input until the user confirms loading the saved version", async () => {
    saveIncomingDraftRow.mockRejectedValue(
      Object.assign(new AxiosError("conflict"), { response: { status: 409 } }),
    );
    listIncomingDraftRows.mockImplementation(() => [
      {
        id: saveIncomingDraftRow.mock.calls[0]?.[1],
        revision: 4,
        catalog_name: null,
        fields: {
          catalog_id: "",
          batch_number: "",
          manufactured_at: "",
          expires_at: "",
          qty: "20",
          purchase_price: "",
          sale_price: "",
        },
      },
    ]);
    renderForm();
    fireEvent.change(screen.getByLabelText("Количество единиц"), { target: { value: "12," } });
    fireEvent.click(await screen.findByRole("button", { name: "Загрузить сохранённое" }));
    expect(screen.getByLabelText("Количество единиц")).toHaveValue("12,");
    const dialog = screen.getByRole("dialog", { name: "Загрузить сохранённый черновик?" });
    expect(listIncomingDraftRows).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Загрузить сохранённое" }));
    await waitFor(() => expect(screen.getByLabelText("Количество единиц")).toHaveValue("20"));
  });

  it("protects confirmed reload from cancellation, repeat requests and new edits while pending", async () => {
    saveIncomingDraftRow.mockRejectedValue(
      Object.assign(new AxiosError("conflict"), { response: { status: 409 } }),
    );
    let resolveRows!: (rows: unknown[]) => void;
    listIncomingDraftRows.mockImplementation(
      () =>
        new Promise<unknown[]>((resolve) => {
          resolveRows = resolve;
        }),
    );
    renderForm();
    fireEvent.change(screen.getByLabelText("Количество единиц"), { target: { value: "12," } });
    fireEvent.click(await screen.findByRole("button", { name: "Загрузить сохранённое" }));
    const dialog = screen.getByRole("dialog", { name: "Загрузить сохранённый черновик?" });
    const confirm = within(dialog).getByRole("button", { name: "Загрузить сохранённое" });
    fireEvent.click(confirm);
    expect(confirm).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Отмена" })).toBeDisabled();
    expect(screen.getByLabelText("Количество единиц")).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));
    fireEvent.click(confirm);
    expect(dialog).toBeInTheDocument();
    expect(listIncomingDraftRows).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveRows([
        {
          id: saveIncomingDraftRow.mock.calls[0]?.[1],
          revision: 4,
          catalog_name: null,
          fields: {
            catalog_id: "",
            batch_number: "",
            manufactured_at: "",
            expires_at: "",
            qty: "20",
            purchase_price: "",
            sale_price: "",
          },
        },
      ]);
    });
    expect(screen.getByLabelText("Количество единиц")).toHaveValue("20");
    expect(screen.getByLabelText("Количество единиц")).toBeEnabled();
  });
});

describe("NewIncomingForm", () => {
  beforeEach(() => {
    updateIncoming.mockReset();
    listBranches.mockReset();
    searchSupplierOptions.mockReset();
    navigate.mockReset();
    listBranches.mockResolvedValue([BRANCH]);
    searchSupplierOptions.mockResolvedValue({
      items: [{ id: SUPPLIER.id, name: SUPPLIER.name, is_active: true }],
    });
    updateIncoming.mockResolvedValue({ id: "doc-1" });
  });

  function renderNewForm(
    document?: React.ComponentProps<typeof NewIncomingForm>["document"],
    onClose = vi.fn(),
  ): void {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <NewIncomingForm document={document} onClose={onClose} />
      </QueryClientProvider>,
    );
  }

  it("explains a failed reference lookup and lets the user retry", async () => {
    listBranches.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce([BRANCH]);
    renderNewForm();

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить точки");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));

    await waitFor(() => expect(listBranches).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("option", { name: BRANCH.name })).toBeInTheDocument();
  });

  it("updates the editable document and explicitly clears its number", async () => {
    const onClose = vi.fn();
    renderNewForm(
      {
        id: "doc-1",
        tenant_id: "tenant-1",
        branch_id: BRANCH.id,
        supplier_id: SUPPLIER.id,
        document_number: "ПР-1",
        document_date: "2026-08-01",
        status: "draft",
        total_amount: "0.00",
        currency: "TJS",
        notes: null,
        document_file_path: null,
        created_at: "2026-08-01T00:00:00Z",
        updated_at: "2026-08-01T00:00:00Z",
        accepted_at: null,
      },
      onClose,
    );

    await screen.findByRole("option", { name: BRANCH.name });
    fireEvent.change(screen.getByLabelText("Номер документа поставщика"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(updateIncoming).toHaveBeenCalledWith(
        "doc-1",
        expect.objectContaining({ document_number: null }),
      ),
    );
    expect(onClose).toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });
});
