import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const searchAudit = vi.fn();
const authState = vi.hoisted(() => ({
  user: {
    is_developer: false,
    is_administrator: false,
    home_tenant_id: null as string | null,
    platform_capabilities: [] as string[],
  },
}));

vi.mock("@/features/audit/api", () => ({
  searchAudit: (...a: unknown[]) => searchAudit(...a),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => authState,
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
  action: "UPDATE",
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
    authState.user = {
      is_developer: false,
      is_administrator: false,
      home_tenant_id: null,
      platform_capabilities: [],
    };
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

  it("defaults an unscoped developer to global audit", async () => {
    authState.user = {
      is_developer: true,
      is_administrator: false,
      home_tenant_id: null,
      platform_capabilities: ["platform.audit.global.view"],
    };
    searchAudit.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 50 });

    renderPage();

    expect(await screen.findByText(/События пока не записаны/i)).toBeInTheDocument();
    expect(searchAudit).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "global", page: 1, page_size: 50 }),
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
    const row = await screen.findByRole("row", { name: /Открыть событие: Обновление, Точка/i });
    expect(within(row).getByText("Обновление")).toBeInTheDocument();
    expect(within(row).getByText("Точка")).toBeInTheDocument();
    expect(screen.getByText(/Найдено: 1/i)).toBeInTheDocument();
    expect(screen.getByText("Изменения на странице")).toBeInTheDocument();
  });

  it("shows rejected authorization changes as events requiring attention", async () => {
    searchAudit.mockResolvedValueOnce({
      items: [
        {
          ...ENTRY,
          action: "AUTHORIZATION_DENIED",
          table_name: "authorization_policy",
          metadata: { reason: "self_assignment_denied" },
        },
      ],
      total: 1,
      page: 1,
      page_size: 50,
    });

    renderPage();

    const row = await screen.findByRole("row", {
      name: /Открыть событие: Опасное действие отклонено, Политика доступа/i,
    });
    expect(within(row).getByText("Опасное действие отклонено")).toBeInTheDocument();
    expect(screen.getByText("Требуют внимания")).toBeInTheDocument();
  });

  it("opens the entry modal with a colored diff on row click", async () => {
    searchAudit.mockResolvedValueOnce({
      items: [ENTRY],
      total: 1,
      page: 1,
      page_size: 50,
    });
    renderPage();
    fireEvent.click(
      await screen.findByRole("row", { name: /Открыть событие: Обновление, Точка/i }),
    );
    expect(await screen.findByText(/Точка · Обновление/i)).toBeInTheDocument();
    // The diff shows the old and new value of the changed field.
    expect(screen.getByText("Old")).toBeInTheDocument();
    expect(screen.getByText("New")).toBeInTheDocument();
  });

  it("opens the entry modal from the keyboard", async () => {
    searchAudit.mockResolvedValueOnce({
      items: [ENTRY],
      total: 1,
      page: 1,
      page_size: 50,
    });
    renderPage();

    const row = await screen.findByRole("row", { name: /Открыть событие: Обновление, Точка/i });
    fireEvent.keyDown(row, { key: "Enter" });

    expect(await screen.findByText(/Точка · Обновление/i)).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    fireEvent.change(screen.getByLabelText(/^Действие$/), {
      target: { value: "DELETE" },
    });
    await waitFor(() => {
      expect(searchAudit).toHaveBeenLastCalledWith(expect.objectContaining({ action: "DELETE" }));
    });
  });

  it("passes calendar dates to the API without converting them through UTC", async () => {
    searchAudit.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 50,
    });
    renderPage();
    await screen.findByText(/События пока не записаны/i);

    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    fireEvent.change(screen.getByLabelText(/^От$/), {
      target: { value: "2026-07-19" },
    });
    fireEvent.change(screen.getByLabelText(/^До$/), {
      target: { value: "2026-07-19" },
    });

    await waitFor(() => {
      expect(searchAudit).toHaveBeenLastCalledWith(
        expect.objectContaining({
          date_from: "2026-07-19",
          date_to: "2026-07-19",
        }),
      );
    });
  });

  it("does not expose global audit to an Aurum administrator", async () => {
    authState.user = {
      is_developer: false,
      is_administrator: true,
      home_tenant_id: null,
      platform_capabilities: [],
    };
    searchAudit.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 50 });

    renderPage();

    await screen.findByText(/События пока не записаны/i);
    expect(screen.queryByRole("option", { name: "Вся платформа" })).not.toBeInTheDocument();
  });

  it("exposes global audit only with the exact platform capability", async () => {
    authState.user = {
      is_developer: true,
      is_administrator: false,
      home_tenant_id: null,
      platform_capabilities: ["platform.audit.global.view"],
    };
    searchAudit.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 50 });

    renderPage();

    await screen.findByText(/События пока не записаны/i);
    expect(screen.getByRole("option", { name: "Вся платформа" })).toBeInTheDocument();
  });
});
