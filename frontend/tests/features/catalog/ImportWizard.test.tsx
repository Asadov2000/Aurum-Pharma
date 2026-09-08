import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const uploadImport = vi.fn();
const getImportJob = vi.fn();
const rollbackImport = vi.fn();
const previewImport = vi.fn();
const confirmImport = vi.fn();
const listCatalog = vi.fn();
const getCatalogSummary = vi.fn();
const listCatalogForPicker = vi.fn();

vi.mock("@/features/catalog/api", () => ({
  uploadImport: (...a: unknown[]) => uploadImport(...a),
  previewImport: (...a: unknown[]) => previewImport(...a),
  confirmImport: (...a: unknown[]) => confirmImport(...a),
  getImportJob: (...a: unknown[]) => getImportJob(...a),
  rollbackImport: (...a: unknown[]) => rollbackImport(...a),
  listCatalog: (...a: unknown[]) => listCatalog(...a),
}));

vi.mock("@/features/catalog/summaryApi", () => ({
  getCatalogSummary: (...a: unknown[]) => getCatalogSummary(...a),
}));
vi.mock("@/features/catalog/pickerApi", () => ({
  listCatalogForPicker: (...a: unknown[]) => listCatalogForPicker(...a),
}));

import { ImportWizard } from "@/features/catalog/ImportWizard";
import { useCatalogPickerQuery } from "@/features/catalog/pickerQueries";
import { catalogKeys, useCatalogQuery } from "@/features/catalog/queries";
import { useCatalogSummaryQuery } from "@/features/catalog/summaryQueries";
import { type ImportJob } from "@/features/catalog/types";

const JOB: ImportJob = {
  id: "job-1",
  tenant_id: "t-1",
  source_filename: "price.xlsx",
  status: "pending",
  duplicate_strategy: "skip",
  total_rows: null,
  valid_rows: null,
  error_rows: null,
  preview_data: null,
  errors: null,
  created_at: "2026-01-01T00:00:00Z",
  started_at: null,
  finished_at: null,
  expires_at_for_rollback: null,
  rolled_back_at: null,
};

const SUCCESS_JOB: ImportJob = {
  ...JOB,
  status: "success",
  total_rows: 3,
  valid_rows: 3,
  error_rows: 0,
};

const READY_JOB: ImportJob = {
  ...SUCCESS_JOB,
  status: "validating",
  preview_data: [{ brand_name: "Тестовый товар" }],
};

function deferredJob() {
  let resolve!: (job: ImportJob) => void;
  const promise = new Promise<ImportJob>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function renderWizard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <ImportWizard onClose={() => {}} canRollback storageKey="aurum:test:catalog-import" />
    </QueryClientProvider>,
  );
  const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
  return { ...view, input };
}

function CatalogCacheProbe() {
  const catalog = useCatalogQuery({ page: 1 });
  const summary = useCatalogSummaryQuery();
  const picker = useCatalogPickerQuery({ q: "", page: 1, page_size: 20 }, true);
  return (
    <>
      <p>Товаров в списке: {catalog.data?.total}</p>
      <p>Товаров в сводке: {summary.data?.total}</p>
      <p>Подбор товара: {picker.data?.items.map((item) => item.brand_name).join(", ")}</p>
    </>
  );
}

describe("ImportWizard", () => {
  beforeEach(() => {
    window.localStorage.clear();
    uploadImport.mockReset();
    getImportJob.mockReset();
    rollbackImport.mockReset();
    previewImport.mockReset();
    confirmImport.mockReset();
    listCatalog.mockReset();
    getCatalogSummary.mockReset();
    listCatalogForPicker.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("rejects a legacy .xls file with a friendly message and never uploads", async () => {
    const { input } = renderWizard();
    const file = new File(["x"], "price.xls", { type: "application/vnd.ms-excel" });

    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText(/Поддерживаются файлы .xlsx/)).toBeInTheDocument();
    });
    expect(uploadImport).not.toHaveBeenCalled();
  });

  it("accepts an .xlsx file and advances to the preview step", async () => {
    uploadImport.mockResolvedValue(JOB);
    getImportJob.mockResolvedValue(JOB);
    const { input } = renderWizard();
    const file = new File(["x"], "price.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });

    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Проверить файл/ })).toBeInTheDocument();
    });
    expect(uploadImport).toHaveBeenCalledTimes(1);
  });

  it("asks before rolling back a successful import", async () => {
    uploadImport.mockResolvedValueOnce(SUCCESS_JOB);
    getImportJob.mockResolvedValue(SUCCESS_JOB);
    rollbackImport.mockResolvedValueOnce({
      ...SUCCESS_JOB,
      status: "rolled_back",
      rolled_back_at: "2026-01-01T00:05:00Z",
    });
    const { input } = renderWizard();
    const file = new File(["x"], "price.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });

    fireEvent.change(input, { target: { files: [file] } });

    const rollbackButton = await screen.findByRole("button", {
      name: /^Отменить результаты импорта$/i,
    });
    fireEvent.click(rollbackButton);
    let dialog = await screen.findByRole("dialog", { name: /Отменить результаты импорта/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /Отмена/i }));
    expect(rollbackImport).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /^Отменить результаты импорта$/i }));
    dialog = await screen.findByRole("dialog", { name: /Отменить результаты импорта/i });
    fireEvent.click(
      within(dialog).getByRole("button", { name: /^Перенести созданные товары в архив$/i }),
    );

    await waitFor(() => {
      expect(rollbackImport).toHaveBeenCalledWith("job-1");
    });
  });

  it("waits for background preview before confirming and prevents duplicate submissions", async () => {
    window.localStorage.setItem("aurum:test:catalog-import", JOB.id);
    getImportJob.mockResolvedValue(JOB);
    const previewRequest = deferredJob();
    const confirmRequest = deferredJob();
    previewImport.mockReturnValue(previewRequest.promise);
    confirmImport.mockReturnValue(confirmRequest.promise);
    renderWizard();

    const previewButton = await screen.findByRole("button", { name: "Проверить файл" });
    fireEvent.click(previewButton);
    fireEvent.click(previewButton);
    await waitFor(() => expect(previewButton).toBeDisabled());
    expect(previewImport).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Импортировать товары" })).not.toBeInTheDocument();

    getImportJob.mockResolvedValue(READY_JOB);
    await act(async () => previewRequest.resolve({ ...JOB, status: "previewing" }));
    expect(await screen.findByText("Проверка файла")).toBeInTheDocument();
    expect(screen.getByText(/Товары пока не добавляются/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Закрыть" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Проверить файл" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Импортировать товары" })).not.toBeInTheDocument();

    const confirmButton = await screen.findByRole(
      "button",
      { name: "Импортировать товары" },
      { timeout: 3500 },
    );
    expect(screen.getByText("Тестовый товар")).toBeInTheDocument();
    fireEvent.click(confirmButton);
    fireEvent.click(confirmButton);
    await waitFor(() => expect(confirmButton).toBeDisabled());
    expect(confirmImport).toHaveBeenCalledTimes(1);
    expect(confirmImport).toHaveBeenCalledWith(JOB.id, "skip");
    expect(screen.getByLabelText("Что делать с дубликатами")).toBeDisabled();
    await act(async () => confirmRequest.resolve({ ...READY_JOB, status: "importing" }));
    expect(await screen.findByText("Импорт выполняется")).toBeInTheDocument();
  });

  it("restores background preview after remount and polls through a transient request failure", async () => {
    window.localStorage.setItem("aurum:test:catalog-import", JOB.id);
    getImportJob.mockResolvedValue({ ...JOB, status: "previewing" });
    const view = renderWizard();
    expect(await screen.findByText("Проверка файла")).toBeInTheDocument();
    view.unmount();
    renderWizard();
    expect(await screen.findByText("Проверка файла")).toBeInTheDocument();
    expect(window.localStorage.getItem("aurum:test:catalog-import")).toBe(JOB.id);
    expect(uploadImport).not.toHaveBeenCalled();
    expect(previewImport).not.toHaveBeenCalled();
    getImportJob.mockRejectedValueOnce(new Error("Connection interrupted"));
    getImportJob.mockResolvedValue(READY_JOB);

    expect(
      await screen.findByText(/Не удалось обновить состояние импорта/, {}, { timeout: 3500 }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Импортировать товары" }, { timeout: 3500 }),
    ).toBeEnabled();
    expect(screen.queryByText(/Не удалось обновить состояние импорта/)).not.toBeInTheDocument();
  }, 10_000);

  it("shows every parser message with its source row", async () => {
    window.localStorage.setItem("aurum:test:catalog-import", JOB.id);
    getImportJob.mockResolvedValue({
      ...READY_JOB,
      errors: [{ row: 2, messages: ["brand_name is required", "base_price 'abc' is not a number"] }],
    });
    renderWizard();

    expect(await screen.findByText("Строка 2")).toBeInTheDocument();
    expect(screen.getByText("brand_name is required")).toBeInTheDocument();
    expect(screen.getByText("base_price 'abc' is not a number")).toBeInTheDocument();
    expect(screen.queryByText(/Сложное значение/)).not.toBeInTheDocument();
  });

  it("distinguishes file failures from product errors and treats markup as text", async () => {
    window.localStorage.setItem("aurum:test:catalog-import", JOB.id);
    getImportJob.mockResolvedValue({
      ...JOB,
      status: "failed",
      errors: [
        { row: 0, messages: ["Обработка файла прервана. Загрузите файл повторно."] },
        { row: "Тестовый товар", messages: ["<img src=x onerror=alert(1)>"] },
      ],
    });
    const { container } = renderWizard();

    expect(await screen.findByText("Ошибка файла")).toBeInTheDocument();
    expect(screen.getByText("Товар: Тестовый товар")).toBeInTheDocument();
    expect(screen.getByText("Обработка файла прервана. Загрузите файл повторно.")).toBeInTheDocument();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.queryByText("Строка 0")).not.toBeInTheDocument();
  });

  it("handles malformed details without exposing unrelated fields", async () => {
    window.localStorage.setItem("aurum:test:catalog-import", JOB.id);
    getImportJob.mockResolvedValue({
      ...JOB,
      status: "failed",
      errors: [
        { messages: [null, {}, "", "  ", "Проверьте файл"], internal: "internal-value" },
        { messages: [], message: "Старое описание ошибки" },
        { row: {}, messages: [{ debug: "nested-value" }] },
      ],
    });
    renderWizard();

    expect(await screen.findByText("Проверьте файл")).toBeInTheDocument();
    expect(screen.getByText("Старое описание ошибки")).toBeInTheDocument();
    expect(screen.getByText("Не удалось прочитать описание ошибки.")).toBeInTheDocument();
    expect(screen.queryByText(/internal-value|nested-value|Сложное значение/)).not.toBeInTheDocument();
  });

  it("allows a new upload after a background failure without exposing confirmation", async () => {
    window.localStorage.setItem("aurum:test:catalog-import", JOB.id);
    getImportJob.mockResolvedValue({
      ...JOB,
      status: "failed",
      errors: [{ message: "Превышено время проверки файла" }],
    });
    renderWizard();
    expect(await screen.findByText(/Обработка файла не завершена/)).toBeInTheDocument();
    expect(screen.getByText(/Превышено время проверки файла/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Импортировать товары" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Новый импорт" }));
    expect(screen.getByLabelText("Файл каталога")).toBeEnabled();
    expect(window.localStorage.getItem("aurum:test:catalog-import")).toBeNull();
  });

  it.each(["preview", "confirm"] as const)(
    "reconciles a lost %s response with the server before offering another action",
    async (action) => {
      window.localStorage.setItem("aurum:test:catalog-import", JOB.id);
      const before = action === "preview" ? JOB : READY_JOB;
      const after: ImportJob = {
        ...before,
        status: action === "preview" ? "previewing" : "importing",
      };
      getImportJob.mockResolvedValueOnce(before).mockResolvedValue(after);
      const mutation = action === "preview" ? previewImport : confirmImport;
      mutation.mockRejectedValueOnce(new Error("Response lost"));
      renderWizard();
      const actionName = action === "preview" ? "Проверить файл" : "Импортировать товары";
      fireEvent.click(await screen.findByRole("button", { name: actionName }));
      expect(
        await screen.findByText(action === "preview" ? "Проверка файла" : "Импорт выполняется"),
      ).toBeInTheDocument();
      expect(mutation).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("button", { name: actionName })).not.toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    },
  );

  it("refreshes the visible catalog, summary and picker after the worker completes", async () => {
    window.localStorage.setItem("aurum:test:catalog-import", JOB.id);
    getImportJob.mockResolvedValueOnce({ ...READY_JOB, status: "importing" });
    getImportJob.mockResolvedValue(SUCCESS_JOB);
    listCatalog.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 20 });
    listCatalog.mockResolvedValue({ items: [], total: 3, page: 1, page_size: 20 });
    getCatalogSummary.mockResolvedValueOnce({ total: 0 });
    getCatalogSummary.mockResolvedValue({ total: 3 });
    listCatalogForPicker.mockResolvedValueOnce({ items: [] });
    listCatalogForPicker.mockResolvedValue({
      items: [{ id: "imported-item", brand_name: "Импортированный товар" }],
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <CatalogCacheProbe />
        <ImportWizard onClose={() => {}} canRollback storageKey="aurum:test:catalog-import" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Товаров в списке: 0")).toBeInTheDocument();
    expect(await screen.findByText("Импорт выполняется")).toBeInTheDocument();
    expect(listCatalog).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Импорт завершён", {}, { timeout: 3500 })).toBeInTheDocument();
    expect(await screen.findByText("Товаров в списке: 3")).toBeInTheDocument();
    expect(await screen.findByText("Товаров в сводке: 3")).toBeInTheDocument();
    expect(await screen.findByText("Подбор товара: Импортированный товар")).toBeInTheDocument();

    await act(async () => {
      qc.setQueryData(catalogKeys.import(JOB.id), {
        ...SUCCESS_JOB,
        finished_at: "2026-01-01T00:05:00Z",
      });
    });
    expect(listCatalog).toHaveBeenCalledTimes(2);
    expect(getCatalogSummary).toHaveBeenCalledTimes(2);
    expect(listCatalogForPicker).toHaveBeenCalledTimes(2);
  });

  it.each([
    { ...JOB, status: "validating" as const },
    { ...READY_JOB, valid_rows: 0, error_rows: 3 },
  ])("does not confirm an incomplete or entirely invalid preview", async (job) => {
    window.localStorage.setItem("aurum:test:catalog-import", JOB.id);
    getImportJob.mockResolvedValue(job);
    renderWizard();
    const button = await screen.findByRole("button", { name: "Импортировать товары" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(confirmImport).not.toHaveBeenCalled();
  });
});
