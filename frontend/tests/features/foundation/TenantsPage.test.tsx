import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listTenants = vi.fn();
const createTenant = vi.fn();

vi.mock("@/features/foundation/api", () => ({
  listTenants: (...a: unknown[]) => listTenants(...a),
  createTenant: (...a: unknown[]) => createTenant(...a),
  updateTenant: vi.fn(),
  getTenantSettings: vi.fn(),
  updateTenantSettings: vi.fn(),
  listBranches: vi.fn(),
  createBranch: vi.fn(),
  updateBranch: vi.fn(),
  deleteBranch: vi.fn(),
  listRegisters: vi.fn(),
  createRegister: vi.fn(),
  updateRegister: vi.fn(),
  deleteRegister: vi.fn(),
}));

import { TenantsPage } from "@/features/foundation/TenantsPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TenantsPage />
    </QueryClientProvider>,
  );
}

const SAMPLE = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Demo Pharmacy",
  legal_name: null,
  inn_or_tin: null,
  registration_number: null,
  contact_email: "owner@aurum.tj",
  contact_phone: null,
  legal_address: null,
  logo_url: null,
  status: "active" as const,
  setup_started_at: "2026-05-22T00:00:00Z",
  trial_started_at: null,
  trial_ends_at: null,
  drug_catalog_mode: "tenant_only",
  created_at: "2026-05-22T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
};

describe("TenantsPage", () => {
  beforeEach(() => {
    listTenants.mockReset();
    createTenant.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the empty state when API returns no tenants", async () => {
    listTenants.mockResolvedValueOnce([]);
    renderPage();
    expect(await screen.findByText(/Пока нет ни одного тенанта/i)).toBeInTheDocument();
  });

  it("renders tenants returned from the API", async () => {
    listTenants.mockResolvedValueOnce([SAMPLE]);
    renderPage();
    expect(await screen.findByText("Demo Pharmacy")).toBeInTheDocument();
    expect(screen.getByText("owner@aurum.tj")).toBeInTheDocument();
    expect(screen.getByText(/Активен/)).toBeInTheDocument();
  });

  it("validates required fields when submitting an empty create form", async () => {
    listTenants.mockResolvedValueOnce([]);
    renderPage();
    await screen.findByText(/Пока нет ни одного тенанта/i);
    fireEvent.click(screen.getByRole("button", { name: /Новый тенант/i }));
    const submit = await screen.findByRole("button", { name: /^Создать$/i });
    fireEvent.click(submit);
    expect(await screen.findByText(/Введите название/i)).toBeInTheDocument();
    expect(createTenant).not.toHaveBeenCalled();
  });

  it("submits a new tenant with trimmed payload", async () => {
    listTenants.mockResolvedValue([]);
    createTenant.mockResolvedValueOnce(SAMPLE);
    renderPage();
    await screen.findByText(/Пока нет ни одного тенанта/i);
    fireEvent.click(screen.getByRole("button", { name: /Новый тенант/i }));
    fireEvent.change(await screen.findByLabelText("Название"), {
      target: { value: " Demo Pharmacy " },
    });
    fireEvent.change(screen.getByLabelText(/Контактный email/i), {
      target: { value: "owner@aurum.tj" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Создать$/i }));
    await waitFor(() => {
      expect(createTenant).toHaveBeenCalledTimes(1);
    });
    expect(createTenant).toHaveBeenCalledWith(
      expect.objectContaining({
        name: " Demo Pharmacy ",
        contact_email: "owner@aurum.tj",
        legal_name: null,
        inn_or_tin: null,
      }),
    );
  });
});
