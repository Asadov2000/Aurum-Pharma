import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const searchAudit = vi.fn();

vi.mock("@/features/audit/api", () => ({
  searchAudit: (...a: unknown[]) => searchAudit(...a),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: { is_developer: false, is_administrator: false },
  }),
}));

import { AuditPage } from "@/features/audit/AuditPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AuditPage />
    </QueryClientProvider>,
  );
}

const ENTRY = {
  id: "a-1",
  tenant_id: "t-1",
  user_id: "u-1",
  action: "update",
  table_name: "branch",
  record_id: "b-1",
  old_values: { name: "Old", is_active: true },
  new_values: { name: "New", is_active: true },
  changed_fields: { name: { old: "Old", new: "New" } },
  ip_address: "127.0.0.1",
  user_agent: "ua/1.0",
  metadata: null,
  created_at: "2026-05-24T12:00:00Z",
};

describe("AuditPage", () => {
  beforeEach(() => {
    searchAudit.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("defaults to 'my' scope and renders an empty hint when nothing returned", async () => {
    searchAudit.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 50 });
    renderPage();
    expect(await screen.findByText(/События пока не записаны/i)).toBeInTheDocument();
    expect(searchAudit).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "my", page: 1, page_size: 50 }),
    );
  });

  it("renders entries with ru action and table labels", async () => {
    searchAudit.mockResolvedValueOnce({
      items: [ENTRY],
      total: 1,
      page: 1,
      page_size: 50,
    });
    renderPage();
    expect(await screen.findByText("Обновление")).toBeInTheDocument();
    expect(screen.getByText("Точка")).toBeInTheDocument();
    expect(screen.getByText(/всего: 1/i)).toBeInTheDocument();
  });

  it("opens the entry modal with a colored diff on row click", async () => {
    searchAudit.mockResolvedValueOnce({
      items: [ENTRY],
      total: 1,
      page: 1,
      page_size: 50,
    });
    renderPage();
    fireEvent.click(await screen.findByText("Обновление"));
    expect(await screen.findByText(/Точка · Обновление/i)).toBeInTheDocument();
    // The diff shows the old and new value of the changed field.
    expect(screen.getByText("Old")).toBeInTheDocument();
    expect(screen.getByText("New")).toBeInTheDocument();
  });

  it("re-queries with action filter typed in", async () => {
    searchAudit.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 50,
    });
    renderPage();
    await screen.findByText(/События пока не записаны/i);
    fireEvent.change(screen.getByLabelText(/^Действие$/), {
      target: { value: "delete" },
    });
    await waitFor(() => {
      expect(searchAudit).toHaveBeenLastCalledWith(
        expect.objectContaining({ action: "delete" }),
      );
    });
  });
});
