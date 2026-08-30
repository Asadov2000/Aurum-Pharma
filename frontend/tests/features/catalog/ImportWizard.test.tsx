import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const uploadImport = vi.fn();
const getImportJob = vi.fn();
const rollbackImport = vi.fn();

vi.mock("@/features/catalog/api", () => ({
  uploadImport: (...a: unknown[]) => uploadImport(...a),
  previewImport: vi.fn(),
  confirmImport: vi.fn(),
  getImportJob: (...a: unknown[]) => getImportJob(...a),
  rollbackImport: (...a: unknown[]) => rollbackImport(...a),
}));

import { ImportWizard } from "@/features/catalog/ImportWizard";
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

describe("ImportWizard", () => {
  beforeEach(() => {
    window.localStorage.clear();
    uploadImport.mockReset();
    getImportJob.mockReset();
    rollbackImport.mockReset();
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
});
