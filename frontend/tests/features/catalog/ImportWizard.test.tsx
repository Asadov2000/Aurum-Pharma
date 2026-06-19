import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const uploadImport = vi.fn();
const getImportJob = vi.fn();

vi.mock("@/features/catalog/api", () => ({
  uploadImport: (...a: unknown[]) => uploadImport(...a),
  previewImport: vi.fn(),
  confirmImport: vi.fn(),
  getImportJob: (...a: unknown[]) => getImportJob(...a),
  rollbackImport: vi.fn(),
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

function renderWizard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <ImportWizard onClose={() => {}} />
    </QueryClientProvider>,
  );
  const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
  return { ...view, input };
}

describe("ImportWizard", () => {
  beforeEach(() => {
    uploadImport.mockReset();
    getImportJob.mockReset();
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
      expect(screen.getByRole("button", { name: /Подготовить превью/ })).toBeInTheDocument();
    });
    expect(uploadImport).toHaveBeenCalledTimes(1);
  });
});
