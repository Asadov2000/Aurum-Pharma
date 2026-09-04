import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getIncoming = vi.fn();
const acceptIncoming = vi.fn();
const rejectIncoming = vi.fn();
const deleteIncomingItem = vi.fn();

let permissions = ["incoming.view", "incoming.create", "incoming.finalize"];
let permissionScopes: Record<string, string[] | null> = {};

vi.mock("@/features/incoming/api", () => ({
  getIncoming: (...args: unknown[]) => getIncoming(...args),
  acceptIncoming: (...args: unknown[]) => acceptIncoming(...args),
  rejectIncoming: (...args: unknown[]) => rejectIncoming(...args),
  deleteIncomingItem: (...args: unknown[]) => deleteIncomingItem(...args),
  addIncomingItem: vi.fn(),
  updateIncomingItem: vi.fn(),
  createIncoming: vi.fn(),
  updateIncoming: vi.fn(),
  listIncoming: vi.fn(),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: { is_developer: false, permissions, permission_scopes: permissionScopes },
  }),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a href="/incoming">{children}</a>,
  useNavigate: () => vi.fn(),
  useParams: () => ({ id: "doc-1" }),
}));

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

function renderPage(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <IncomingDetailPage />
    </QueryClientProvider>,
  );
}

describe("IncomingDetailPage", () => {
  beforeEach(() => {
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

    fireEvent.click(await screen.findByRole("button", { name: "Принять на склад" }));
    const dialog = screen.getByRole("dialog", { name: "Принять товары на склад" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад" }));

    await waitFor(() => expect(acceptIncoming).toHaveBeenCalledWith("doc-1"));
    expect(await within(dialog).findByText("Не удалось принять приход")).toBeInTheDocument();
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
});
