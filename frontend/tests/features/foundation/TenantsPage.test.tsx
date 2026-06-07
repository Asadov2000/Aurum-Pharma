import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listTenants = vi.fn();
const createTenant = vi.fn();
const createTenantOwner = vi.fn();

vi.mock("@/features/foundation/api", () => ({
  listTenants: (...a: unknown[]) => listTenants(...a),
  createTenant: (...a: unknown[]) => createTenant(...a),
  createTenantOwner: (...a: unknown[]) => createTenantOwner(...a),
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
    createTenantOwner.mockReset();
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
    fireEvent.click(screen.getByRole("button", { name: /Новая аптека/i }));
    const submit = await screen.findByRole("button", { name: /Создать аптеку и владельца/i });
    fireEvent.click(submit);
    // Pharmacy AND owner fields are required.
    expect(await screen.findByText(/Введите название/i)).toBeInTheDocument();
    expect(screen.getByText(/Введите ФИО владельца/i)).toBeInTheDocument();
    expect(screen.getByText(/Некорректный email владельца/i)).toBeInTheDocument();
    expect(createTenant).not.toHaveBeenCalled();
    expect(createTenantOwner).not.toHaveBeenCalled();
  });

  it("creates pharmacy + owner and shows the login-code helper", async () => {
    listTenants.mockResolvedValue([]);
    createTenant.mockResolvedValueOnce(SAMPLE);
    createTenantOwner.mockResolvedValueOnce({
      user_id: "u-1",
      email: "vladelec@shifo.tj",
      home_tenant_id: SAMPLE.id,
      role_id: "r-1",
    });
    renderPage();
    await screen.findByText(/Пока нет ни одного тенанта/i);
    fireEvent.click(screen.getByRole("button", { name: /Новая аптека/i }));
    fireEvent.change(await screen.findByLabelText("Название"), {
      target: { value: " Demo Pharmacy " },
    });
    fireEvent.change(screen.getByLabelText(/Контактный email/i), {
      target: { value: "owner@aurum.tj" },
    });
    fireEvent.change(screen.getByLabelText(/ФИО владельца/i), {
      target: { value: "Владелец Аптеки" },
    });
    fireEvent.change(screen.getByLabelText(/Email владельца/i), {
      target: { value: "vladelec@shifo.tj" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Создать аптеку и владельца/i }));

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
    expect(createTenantOwner).toHaveBeenCalledWith(SAMPLE.id, {
      email: "vladelec@shifo.tj",
      full_name: "Владелец Аптеки",
    });
    // Success panel: owner email + login-code helper.
    expect(await screen.findByText(/Аптека и владелец созданы/i)).toBeInTheDocument();
    expect(screen.getByText("vladelec@shifo.tj")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Получить код входа/i })).toBeInTheDocument();
  });
});
