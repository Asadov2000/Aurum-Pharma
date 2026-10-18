import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const saleControl = vi.hoisted(() => ({
  blocked: false,
  hasDraft: false,
  reportReady: true,
  renderedRegisterIds: [] as string[],
}));
const listRegisters = vi.fn();

vi.mock("@/features/foundation/api", () => ({
  listRegisters: (...args: unknown[]) => listRegisters(...args),
  listBranches: vi.fn().mockResolvedValue([]),
  listTenants: vi.fn(),
  createTenant: vi.fn(),
  updateTenant: vi.fn(),
  getTenantOperationalSettings: vi.fn().mockResolvedValue({
    draft_sale_lifetime_min: 60,
    pos_payment_methods: ["cash", "card", "qr"],
    pos_mixed_payment_enabled: true,
  }),
  updateTenantSettings: vi.fn(),
  createBranch: vi.fn(),
  updateBranch: vi.fn(),
  deleteBranch: vi.fn(),
  createRegister: vi.fn(),
  updateRegister: vi.fn(),
  deleteRegister: vi.fn(),
}));

vi.mock("@/features/pos/SaleArea", async () => {
  const { useEffect } = await import("react");
  return {
    SaleArea: ({
      registerId,
      registerAvailable,
      canSell,
      canOpenShift,
      workstationControls,
      onRegisterSwitchStateChange,
    }: {
      registerId: string;
      registerAvailable?: boolean;
      canSell: boolean;
      canOpenShift: boolean;
      workstationControls: ReactNode;
      onRegisterSwitchStateChange: (state: { blocked: boolean; hasDraft: boolean }) => void;
    }) => {
      saleControl.renderedRegisterIds.push(registerId);
      const { blocked, hasDraft, reportReady } = saleControl;
      useEffect(() => {
        if (reportReady) onRegisterSwitchStateChange({ blocked, hasDraft });
      }, [blocked, hasDraft, reportReady, onRegisterSwitchStateChange]);
      return (
        <section>
          {workstationControls}
          <output aria-label="Фактическая касса">{registerId}</output>
          <output aria-label="Касса доступна">{String(registerAvailable)}</output>
          <output aria-label="Разрешение продажи">{String(canSell)}</output>
          <output aria-label="Разрешение открытия смены">{String(canOpenShift)}</output>
        </section>
      );
    },
  };
});

import { foundationKeys } from "@/features/foundation/queries";
import { type Register } from "@/features/foundation/types";
import { POSPage } from "@/features/pos/POSPage";
import {
  defaultDevicePreferences,
  devicePreferencesScope,
  loadDevicePreferences,
  saveDevicePreferences,
} from "@/lib/devicePreferences";
import { useAuthStore } from "@/stores/auth";

import { authenticatePos, POS_SCOPE } from "./posIdentityFixture";

const REGISTER_A: Register = {
  id: "register-a",
  tenant_id: POS_SCOPE.tenantId,
  branch_id: "branch-a",
  name: "Касса А",
  printer_type: null,
  printer_config: null,
  card_terminal_id: null,
  qr_terminal_id: null,
  is_active: true,
  created_at: "2026-10-18T00:00:00Z",
  updated_at: "2026-10-18T00:00:00Z",
};
const REGISTER_B: Register = {
  ...REGISTER_A,
  id: "register-b",
  branch_id: "branch-b",
  name: "Касса Б",
};
const REGISTER_C: Register = { ...REGISTER_A, id: "register-c", name: "Касса В" };
const DEVICE_SCOPE = devicePreferencesScope(POS_SCOPE.userId, POS_SCOPE.tenantId);

async function renderPage(registers: Register[] = [REGISTER_A, REGISTER_B]) {
  listRegisters.mockResolvedValue(registers);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const element = (
    <QueryClientProvider client={queryClient}>
      <POSPage />
    </QueryClientProvider>
  );
  const view = render(element);
  await waitFor(() => expect(listRegisters).toHaveBeenCalled());
  await screen.findByLabelText("Касса");
  return {
    queryClient,
    async refreshRegisters(next: Register[]) {
      const previousRenderCount = saleControl.renderedRegisterIds.length;
      await act(async () => {
        queryClient.setQueryData(foundationKeys.registers(null, false), next);
      });
      await waitFor(() =>
        expect(saleControl.renderedRegisterIds.length).toBeGreaterThan(previousRenderCount),
      );
    },
    async reportState(
      state: Partial<Pick<typeof saleControl, "blocked" | "hasDraft" | "reportReady">>,
    ) {
      Object.assign(saleControl, state);
      await act(async () => {
        view.rerender(
          <QueryClientProvider client={queryClient}>
            <POSPage />
          </QueryClientProvider>,
        );
      });
    },
  };
}

async function expectRegister(register: Register) {
  expect(screen.getByLabelText("Фактическая касса")).toHaveTextContent(register.id);
  await waitFor(() => expect(loadDevicePreferences(DEVICE_SCOPE).lastRegisterId).toBe(register.id));
}

function requestRegister(register: Register) {
  fireEvent.change(screen.getByLabelText("Касса"), { target: { value: register.id } });
}

describe("POSPage register switch recovery", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAuthStore.getState().setUser({
      ...authenticatePos(),
      permissions: ["pos.sell", "pos.shift_open", "pos.shift_close"],
      permission_scopes: {
        "pos.sell": null,
        "pos.shift_open": null,
        "pos.shift_close": null,
      },
    });
    saveDevicePreferences(DEVICE_SCOPE, {
      ...defaultDevicePreferences(),
      lastRegisterId: REGISTER_A.id,
    });
    saleControl.blocked = false;
    saleControl.hasDraft = false;
    saleControl.reportReady = true;
    saleControl.renderedRegisterIds.length = 0;
    listRegisters.mockReset();
  });

  afterEach(() => {
    act(() => useAuthStore.getState().clear());
    vi.clearAllMocks();
  });

  it.each([
    { name: "one replacement", registers: [REGISTER_B] },
    { name: "no replacement", registers: [] },
    { name: "multiple replacements", registers: [REGISTER_B, REGISTER_C] },
  ])("preserves a busy register after refresh with $name", async ({ registers }) => {
    saleControl.blocked = true;
    const page = await renderPage();
    await expectRegister(REGISTER_A);

    await page.refreshRegisters(registers);

    await expectRegister(REGISTER_A);
    expect(screen.getByLabelText("Касса доступна")).toHaveTextContent("false");
    expect(screen.getByLabelText("Разрешение продажи")).toHaveTextContent("false");
    expect(screen.getByLabelText("Касса")).not.toHaveDisplayValue(REGISTER_B.name);
    expect(screen.getByLabelText("Касса")).toBeDisabled();
    expect(saleControl.renderedRegisterIds).not.toContain(REGISTER_B.id);
  });

  it("requires explicit confirmation before replacing a missing register with a saved draft", async () => {
    saleControl.hasDraft = true;
    const page = await renderPage();
    await page.refreshRegisters([REGISTER_B]);

    await expectRegister(REGISTER_A);
    expect(screen.getByLabelText("Касса доступна")).toHaveTextContent("false");
    expect(screen.getByLabelText("Разрешение продажи")).toHaveTextContent("false");
    expect(screen.getByLabelText("Касса")).not.toHaveDisplayValue(REGISTER_B.name);
    requestRegister(REGISTER_B);
    expect(
      await screen.findByRole("dialog", { name: "Перейти на другую кассу?" }),
    ).toBeInTheDocument();
    await expectRegister(REGISTER_A);

    fireEvent.click(screen.getByRole("button", { name: "Сохранить чек и перейти" }));

    await expectRegister(REGISTER_B);
    expect(screen.getByLabelText("Касса доступна")).toHaveTextContent("true");
  });

  it.each([
    { name: "empty", registers: [] },
    { name: "multiple", registers: [REGISTER_B, REGISTER_C] },
  ])(
    "preserves a missing register with a draft in an $name refreshed list",
    async ({ registers }) => {
      saleControl.hasDraft = true;
      const page = await renderPage();
      await page.refreshRegisters(registers);

      await expectRegister(REGISTER_A);
      expect(screen.getByLabelText("Касса доступна")).toHaveTextContent("false");
      expect(screen.getByLabelText("Касса")).not.toHaveValue("");
    },
  );

  it("does not confirm a pending register change while an operation becomes busy", async () => {
    saleControl.hasDraft = true;
    const page = await renderPage();
    requestRegister(REGISTER_B);
    expect(
      await screen.findByRole("dialog", { name: "Перейти на другую кассу?" }),
    ).toBeInTheDocument();
    await page.reportState({ blocked: true });

    const confirm = screen.queryByRole("button", { name: "Сохранить чек и перейти" });
    if (confirm) fireEvent.click(confirm);

    await expectRegister(REGISTER_A);
    expect(saleControl.renderedRegisterIds).not.toContain(REGISTER_B.id);
  });

  it("does not confirm a target that disappears while the confirmation is open", async () => {
    saleControl.hasDraft = true;
    const page = await renderPage();
    requestRegister(REGISTER_B);
    expect(
      await screen.findByRole("dialog", { name: "Перейти на другую кассу?" }),
    ).toBeInTheDocument();
    await page.refreshRegisters([REGISTER_A, REGISTER_C]);

    const confirm = screen.queryByRole("button", { name: "Сохранить чек и перейти" });
    if (confirm) fireEvent.click(confirm);

    await expectRegister(REGISTER_A);
    expect(saleControl.renderedRegisterIds).not.toContain(REGISTER_B.id);
  });

  it("ignores an unavailable manual target and a choice of the current register", async () => {
    await renderPage();
    const selector = screen.getByLabelText<HTMLSelectElement>("Касса");
    // Model an option chosen from stale UI after its register was removed from the query.
    const staleOption = new Option(REGISTER_C.name, REGISTER_C.id);
    selector.add(staleOption);
    fireEvent.change(selector, { target: { value: REGISTER_C.id } });
    staleOption.remove();
    await expectRegister(REGISTER_A);
    requestRegister(REGISTER_A);
    await expectRegister(REGISTER_A);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("waits for the restored register workspace before automatically choosing its replacement", async () => {
    saleControl.reportReady = false;
    const page = await renderPage([REGISTER_B]);

    await expectRegister(REGISTER_A);
    expect(saleControl.renderedRegisterIds).not.toContain(REGISTER_B.id);
    await page.reportState({ blocked: true, reportReady: true });
    await expectRegister(REGISTER_A);

    await page.reportState({ blocked: false });

    await expectRegister(REGISTER_B);
    expect(screen.getByLabelText("Касса доступна")).toHaveTextContent("true");
  });

  it("automatically switches once recovery and the draft are both resolved", async () => {
    saleControl.blocked = true;
    saleControl.hasDraft = true;
    const page = await renderPage();
    await page.refreshRegisters([REGISTER_B]);
    await page.reportState({ blocked: false });
    await expectRegister(REGISTER_A);

    await page.reportState({ hasDraft: false });

    await expectRegister(REGISTER_B);
    expect(screen.getByLabelText("Касса")).toHaveDisplayValue(REGISTER_B.name);
  });

  it("automatically selects the only available register on a fresh workstation", async () => {
    saveDevicePreferences(DEVICE_SCOPE, defaultDevicePreferences());

    await renderPage([REGISTER_B]);

    await expectRegister(REGISTER_B);
    expect(screen.getByLabelText("Касса доступна")).toHaveTextContent("true");
    expect(screen.getByLabelText("Разрешение продажи")).toHaveTextContent("true");
  });

  it("preserves a listed busy register without granting sales through a different branch", async () => {
    saleControl.blocked = true;
    saleControl.hasDraft = true;
    useAuthStore.getState().setUser({
      ...authenticatePos(),
      permissions: ["pos.sell", "pos.shift_open", "pos.shift_close"],
      permission_scopes: {
        "pos.sell": [REGISTER_B.branch_id],
        "pos.shift_open": [REGISTER_B.branch_id],
        "pos.shift_close": [REGISTER_A.branch_id],
      },
    });

    await renderPage();

    await expectRegister(REGISTER_A);
    expect(screen.getByLabelText("Касса доступна")).toHaveTextContent("true");
    expect(screen.getByLabelText("Разрешение продажи")).toHaveTextContent("false");
    expect(screen.getByLabelText("Разрешение открытия смены")).toHaveTextContent("false");
    expect(saleControl.renderedRegisterIds).not.toContain(REGISTER_B.id);
  });
});
