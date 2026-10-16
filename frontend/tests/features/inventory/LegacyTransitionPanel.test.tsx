import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MeResponse } from "@/features/auth/types";
import type { BatchDetails } from "@/features/inventory/types";
import type { LegacyCount, LegacyPreview } from "@/features/inventory/legacyTransition";
import {
  previewLegacyReconciliation,
  saveLegacyReconciliation,
  readLegacyReconciliation,
  postLegacyTransition,
  readLegacyTransition,
} from "@/features/inventory/legacyTransition";
import { LegacyTransitionPanel } from "@/features/inventory/LegacyTransitionPanel";
import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";
import { getAuthEpoch } from "@/features/auth/logoutState";
import { getSupportAccessRevision } from "@/stores/supportAccess";
import { withAuthSession } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  user: null as MeResponse | null,
  mode: "full",
  blocked: [] as string[],
  policyLoaded: true,
  online: true,
  generation: 0,
  post: vi.fn(),
  get: vi.fn(),
}));
vi.mock("@/features/auth/hooks", () => ({ useAuth: () => ({ user: mocks.user }) }));
vi.mock("@/lib/api", async (original) => {
  const actual = await original<typeof import("@/lib/api")>();
  return {
    ...actual,
    withAuthSession: vi.fn(actual.withAuthSession),
    api: {
      post: (...args: unknown[]) => mocks.post(...args),
      get: (...args: unknown[]) => mocks.get(...args),
    },
  };
});
vi.mock("@/features/tenantAccess/queries", () => ({
  useTenantAccessPolicy: () => ({
    data: mocks.policyLoaded
      ? {
          tenant_id: mocks.user?.active_tenant_id,
          mode: mocks.mode,
          blocked_modules: mocks.blocked,
        }
      : undefined,
  }),
}));
vi.mock("@/lib/connectivityContext", () => ({
  useConnectivity: () => ({ canUseServer: mocks.online }),
}));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: (scope: { userId: string; tenantId: string }) => {
    const generation = mocks.generation;
    return () =>
      generation === mocks.generation &&
      mocks.user?.id === scope.userId &&
      mocks.user.active_tenant_id === scope.tenantId;
  },
}));

const id = (value: number): string => `00000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const permissions = ["batches.view", "batches.view_costs", "batches.update", "incoming.view"];
const batch: BatchDetails = {
  id: id(4),
  tenant_id: id(2),
  branch_id: id(3),
  catalog_id: id(5),
  batch_number: "LEGACY-001",
  manufactured_at: null,
  expires_at: "2027-10-20",
  purchase_price: "5.00",
  sale_price: "8.00",
  currency: "TJS",
  qty_initial: "4.000",
  qty_remaining: "4.000",
  is_blocked: false,
  block_reason: null,
  blocked_at: null,
  created_at: "2026-10-15T10:00:00Z",
  updated_at: "2026-10-15T10:00:00Z",
  branch_name: "Аптека 1",
  catalog_name: "Историческая партия",
  catalog_form: "таблетки",
  catalog_dosage: null,
  catalog_pack_size: "Не использовать для восстановления упаковки",
  expiry_status: "normal",
  days_to_expiry: 370,
  report_timezone: "Asia/Dushanbe",
  recent_movements: [],
};
const packaging: LegacyCount["packaging"] = {
  version: 2 as const,
  unit: "tablet" as const,
  units_per_pack: 20,
  units_per_blister: 10,
  dispense_step: 1,
};
function preview(): LegacyPreview {
  const identity = { tenant_id: id(2), branch_id: id(3), catalog_id: id(5), batch_id: id(4) };
  return {
    source: { ...identity, legacy_quantity: "4.000" },
    observation: { ...identity, packaging, closed_packs: 4, opened_packs: [] },
    recorded_base_units: "80.000",
    observed_base_units: 80,
    discrepancy_base_units: "0.000",
    fractional_base_units: "0.000",
    recorded_quantity_representable: true,
    comparison: "matched" as const,
  };
}
function record(recordId = id(6)) {
  return {
    id: recordId,
    created_at: "2026-10-15T10:00:00Z",
    created_by: id(1),
    preview: preview(),
  };
}
function result(commandId = id(7)) {
  return { command_id: commandId, lot_id: id(8), operation_id: id(9), movement_id: id(10) };
}
function authConfig() {
  return {
    _auth: { generation: getAuthSessionGeneration(), epoch: getAuthEpoch() },
    _supportRevision: getSupportAccessRevision(),
  };
}
let client: QueryClient;
function view(details = batch) {
  return (
    <QueryClientProvider client={client}>
      <LegacyTransitionPanel batch={details} />
    </QueryClientProvider>
  );
}
function fill() {
  fireEvent.change(screen.getByLabelText("Единица"), { target: { value: "tablet" } });
  fireEvent.change(screen.getByLabelText("Единиц в упаковке"), { target: { value: "20" } });
  fireEvent.change(screen.getByLabelText("Единиц в блистере (необязательно)"), {
    target: { value: "10" },
  });
  fireEvent.change(screen.getByLabelText("Шаг отпуска"), { target: { value: "1" } });
  fireEvent.change(screen.getByLabelText("Закрытых упаковок"), { target: { value: "4" } });
  fireEvent.click(screen.getByLabelText("Подтверждаю: открытых упаковок нет"));
}
async function review() {
  fill();
  fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Сохранить пересчёт" })).toBeEnabled(),
  );
}
async function save() {
  await review();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить пересчёт" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Перенести остаток" })).toBeEnabled(),
  );
}
function transferCalls() {
  return mocks.post.mock.calls.filter(([path]) => path === `/batches/${batch.id}/unit-transition`);
}
function saveCalls() {
  return mocks.post.mock.calls.filter(
    ([path]) => path === `/batches/${batch.id}/unit-reconciliation/records`,
  );
}
function openConfirmation() {
  fireEvent.click(screen.getByRole("button", { name: "Перенести остаток" }));
  return screen.getByRole("dialog");
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.user = {
    id: id(1),
    active_tenant_id: id(2),
    is_developer: false,
    support_access: null,
    permissions: [...permissions],
    permission_scopes: Object.fromEntries(permissions.map((p) => [p, null])),
  } as MeResponse;
  mocks.mode = "full";
  mocks.blocked = [];
  mocks.policyLoaded = true;
  mocks.online = true;
  mocks.generation = 0;
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  mocks.post.mockImplementation(async (path: string, payload: Record<string, unknown>) => {
    if (path.endsWith("/preview")) return { data: preview() };
    if (path.endsWith("/records")) return { data: record(String(payload.reconciliation_id)) };
    if (path.endsWith("/unit-transition")) return { data: result(String(payload.command_id)) };
    throw new Error(`Unexpected POST ${path}`);
  });
  mocks.get.mockRejectedValue({ response: { status: 404 } });
});
afterEach(() => {
  cleanup();
  client.clear();
  useAuthStore.getState().setUser(null);
});

describe("legacy requests capture authority before transport scheduling", () => {
  it.each(["preview", "save", "read reconciliation", "post transition", "read transition"])(
    "binds auth generation and support revision for %s",
    async (operation) => {
      useAuthStore.getState().setUser(mocks.user);
      const captured = authConfig();
      const context = {
        tenant_id: batch.tenant_id,
        branch_id: batch.branch_id,
        batch_id: batch.id,
        catalog_id: batch.catalog_id,
        actor_id: id(1),
      };
      const count: LegacyCount = { packaging, closed_packs: 4, opened_packs: [] };
      const actions: Record<string, () => Promise<unknown>> = {
        preview: () => previewLegacyReconciliation(context, count),
        save: () => saveLegacyReconciliation(context, count, id(6), "4.000"),
        "read reconciliation": () => readLegacyReconciliation(context, id(6)),
        "post transition": () => postLegacyTransition(context, id(6), id(7)),
        "read transition": () => readLegacyTransition(context, id(7)),
      };
      const post = mocks.post.getMockImplementation()!;
      mocks.post.mockImplementation((...args: unknown[]) => {
        useAuthStore.getState().setUser({ ...mocks.user!, id: id(20) });
        return post(...args);
      });
      mocks.get.mockImplementation(async () => {
        useAuthStore.getState().setUser({ ...mocks.user!, id: id(20) });
        return { data: operation === "read reconciliation" ? record() : result() };
      });
      await actions[operation]();
      expect(withAuthSession).toHaveBeenCalledTimes(1);
      expect(vi.mocked(withAuthSession).mock.calls[0][1]).toBe(true);
      expect(getAuthSessionGeneration()).toBeGreaterThan(captured._auth.generation);
      const request = operation.startsWith("read")
        ? mocks.get.mock.calls[0]
        : mocks.post.mock.calls[0];
      expect(request[operation.startsWith("read") ? 1 : 2]).toEqual({
        ...captured,
        ...(operation.startsWith("read") ? { params: { branch_id: batch.branch_id } } : {}),
      });
    },
  );
});

describe("explicit closed-pack legacy transition", () => {
  it("requires an explicit count, saves its observation and transfers only after confirmation", async () => {
    const invalidate = vi.spyOn(client, "invalidateQueries");
    render(view());
    expect(mocks.post).not.toHaveBeenCalled();
    expect(mocks.get).not.toHaveBeenCalled();
    await review();
    expect(mocks.post).toHaveBeenCalledWith(
      `/batches/${batch.id}/unit-reconciliation/preview`,
      { branch_id: batch.branch_id, packaging, closed_packs: 4, opened_packs: [] },
      authConfig(),
    );
    expect(saveCalls()).toHaveLength(0);
    expect(transferCalls()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить пересчёт" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Перенести остаток" })).toBeEnabled(),
    );
    const saved = saveCalls()[0][1] as Record<string, unknown>;
    expect(saved).toEqual({
      branch_id: batch.branch_id,
      packaging,
      closed_packs: 4,
      opened_packs: [],
      reconciliation_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
      expected_legacy_quantity: "4.000",
    });
    expect(transferCalls()).toHaveLength(0);
    const dialog = openConfirmation();
    expect(transferCalls()).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "Подтвердить перенос" }));
    await waitFor(() => expect(transferCalls()).toHaveLength(1));
    const posted = transferCalls()[0][1] as Record<string, unknown>;
    expect(posted).toEqual({
      branch_id: batch.branch_id,
      reconciliation_id: saved.reconciliation_id,
      command_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    });
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["inventory"] }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["reports"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["pos"] });
  });

  it.each([
    ["Единиц в упаковке", "0"],
    ["Единиц в упаковке", "1"],
    ["Единиц в упаковке", "10001"],
    ["Единиц в упаковке", "1.5"],
    ["Единиц в блистере (необязательно)", "3"],
    ["Шаг отпуска", "3"],
    ["Закрытых упаковок", "-1"],
    ["Закрытых упаковок", "1.5"],
    ["Закрытых упаковок", "5000000000"],
  ])("rejects invalid physical input: %s=%s before any request", async (label, value) => {
    render(view());
    fill();
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
    await screen.findByRole("alert");
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("never infers packaging from current catalog text and requires the no-open-packs declaration", async () => {
    render(view());
    expect(screen.getByLabelText("Единиц в упаковке")).toHaveValue("");
    fill();
    fireEvent.click(screen.getByLabelText("Подтверждаю: открытых упаковок нет"));
    fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
    await screen.findByRole("alert");
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it.each([2, 10000])(
    "accepts the backend packaging boundary %s with explicit unknown blister composition",
    async (units) => {
      const response = preview();
      response.observation.packaging = {
        ...packaging,
        units_per_pack: units,
        units_per_blister: null,
      };
      response.recorded_base_units = `${units * 4}.000`;
      response.observed_base_units = units * 4;
      mocks.post.mockResolvedValue({ data: response });
      render(view());
      fill();
      fireEvent.change(screen.getByLabelText("Единиц в упаковке"), {
        target: { value: String(units) },
      });
      fireEvent.change(screen.getByLabelText("Единиц в блистере (необязательно)"), {
        target: { value: "" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Сохранить пересчёт" })).toBeEnabled(),
      );
      expect(mocks.post).toHaveBeenCalledWith(
        `/batches/${batch.id}/unit-reconciliation/preview`,
        {
          branch_id: batch.branch_id,
          packaging: { ...packaging, units_per_pack: units, units_per_blister: null },
          closed_packs: 4,
          opened_packs: [],
        },
        authConfig(),
      );
      expect(transferCalls()).toHaveLength(0);
    },
  );

  it("cancels confirmation without stock transfer", async () => {
    render(view());
    await save();
    fireEvent.click(within(openConfirmation()).getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(transferCalls()).toHaveLength(0);
  });

  it.each(["shortage", "not representable"])(
    "does not authorize a %s comparison",
    async (reason) => {
      const response = preview();
      if (reason === "shortage")
        Object.assign(response, { comparison: "shortage", discrepancy_base_units: "-20.000" });
      else
        Object.assign(response, {
          recorded_quantity_representable: false,
          fractional_base_units: "0.500",
        });
      mocks.post.mockResolvedValue({ data: response });
      render(view());
      fill();
      fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
      await screen.findByText(/Есть расхождение/);
      expect(screen.getByRole("button", { name: "Сохранить пересчёт" })).toBeDisabled();
      expect(saveCalls()).toHaveLength(0);
      expect(transferCalls()).toHaveLength(0);
    },
  );

  it("invalidates a reviewed preview when the operator changes the count", async () => {
    render(view());
    await review();
    fireEvent.change(screen.getByLabelText("Закрытых упаковок"), { target: { value: "3" } });
    expect(screen.queryByRole("button", { name: "Сохранить пересчёт" })).not.toBeInTheDocument();
    expect(saveCalls()).toHaveLength(0);
  });

  it("recovers a lost save response by its original UUID without a second save or automatic transfer", async () => {
    const post = mocks.post.getMockImplementation()!;
    mocks.post.mockImplementation((path: string, payload: Record<string, unknown>) =>
      path.endsWith("/records")
        ? Promise.reject(new Error("connection lost"))
        : post(path, payload),
    );
    render(view());
    await review();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить пересчёт" }));
    await screen.findByRole("alert");
    const savedId = String((saveCalls()[0][1] as Record<string, unknown>).reconciliation_id);
    expect(screen.getByLabelText("Код пересчёта")).toHaveValue(savedId);
    expect(screen.queryByRole("button", { name: "Сохранить пересчёт" })).not.toBeInTheDocument();
    expect(mocks.get).not.toHaveBeenCalled();
    mocks.get.mockResolvedValue({ data: record(savedId) });
    fireEvent.click(screen.getByRole("button", { name: "Проверить сохранение" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Перенести остаток" })).toBeEnabled(),
    );
    expect(mocks.get).toHaveBeenCalledWith(
      `/batches/${batch.id}/unit-reconciliation/records/${savedId}`,
      { params: { branch_id: batch.branch_id }, ...authConfig() },
    );
    expect(saveCalls()).toHaveLength(1);
    expect(transferCalls()).toHaveLength(0);
  });

  it("keeps an unresolved transfer bound to the same UUID across failed GET checks", async () => {
    const post = mocks.post.getMockImplementation()!;
    mocks.post.mockImplementation((path: string, payload: Record<string, unknown>) =>
      path.endsWith("/unit-transition")
        ? Promise.reject(new Error("connection lost"))
        : post(path, payload),
    );
    render(view());
    await save();
    fireEvent.click(
      within(openConfirmation()).getByRole("button", { name: "Подтвердить перенос" }),
    );
    await screen.findByRole("alert");
    const commandId = String((transferCalls()[0][1] as Record<string, unknown>).command_id);
    expect(screen.getByLabelText("Код переноса")).toHaveValue(commandId);
    expect(screen.getByRole("button", { name: "Перенести остаток" })).toBeDisabled();
    expect(mocks.get).not.toHaveBeenCalled();
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      fireEvent.click(screen.getByRole("button", { name: "Проверить перенос" }));
      await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(attempt));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Проверить перенос" })).toBeEnabled(),
      );
    }
    expect(
      mocks.get.mock.calls.every(
        ([path]) => path === `/batches/${batch.id}/unit-transition/${commandId}`,
      ),
    ).toBe(true);
    expect(transferCalls()).toHaveLength(1);
    mocks.get.mockResolvedValue({ data: result(commandId) });
    fireEvent.click(screen.getByRole("button", { name: "Проверить перенос" }));
    await screen.findByText(/Перенос подтверждён/);
    expect(transferCalls()).toHaveLength(1);
  });

  it("recovers a known transfer after reopening even when the tenant is readonly", async () => {
    mocks.mode = "readonly";
    mocks.get.mockResolvedValue({ data: result() });
    render(view());
    fireEvent.change(screen.getByLabelText("Код переноса"), { target: { value: id(7) } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить перенос" }));
    await screen.findByText(/Перенос подтверждён/);
    expect(mocks.get).toHaveBeenCalledWith(`/batches/${batch.id}/unit-transition/${id(7)}`, {
      params: { branch_id: batch.branch_id },
      ...authConfig(),
    });
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it.each([400, 422])(
    "allows correcting a count after a definitive HTTP %s save rejection",
    async (status) => {
      const post = mocks.post.getMockImplementation()!;
      mocks.post.mockImplementation((path: string, payload: Record<string, unknown>) =>
        path.endsWith("/records")
          ? Promise.reject({ isAxiosError: true, response: { status } })
          : post(path, payload),
      );
      render(view());
      await review();
      fireEvent.click(screen.getByRole("button", { name: "Сохранить пересчёт" }));
      await screen.findByRole("alert");
      expect(screen.getByLabelText("Закрытых упаковок")).toBeEnabled();
      expect(screen.getByRole("button", { name: "Проверить пересчёт" })).toBeEnabled();
      expect(screen.getByLabelText("Код пересчёта")).toHaveValue("");
      expect(screen.queryByRole("button", { name: "Сохранить пересчёт" })).not.toBeInTheDocument();
      expect(saveCalls()).toHaveLength(1);
      expect(transferCalls()).toHaveLength(0);
    },
  );

  it.each(["count", "packaging"])(
    "rejects a preview with a different returned %s",
    async (field) => {
      const response = preview();
      if (field === "count") response.observation.closed_packs = 5;
      else response.observation.packaging = { ...packaging, units_per_pack: 40 };
      mocks.post.mockResolvedValue({ data: response });
      render(view());
      fill();
      fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
      await screen.findByRole("alert");
      expect(screen.queryByRole("button", { name: "Сохранить пересчёт" })).not.toBeInTheDocument();
    },
  );

  it("rejects recovery of another actor's saved observation", async () => {
    mocks.get.mockResolvedValue({ data: { ...record(), created_by: id(20) } });
    render(view());
    fireEvent.change(screen.getByLabelText("Код пересчёта"), { target: { value: id(6) } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить сохранение" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "Перенести остаток" })).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("rejects recovery if the server returns a different transfer command", async () => {
    mocks.get.mockResolvedValue({ data: result(id(20)) });
    render(view());
    fireEvent.change(screen.getByLabelText("Код переноса"), { target: { value: id(7) } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить перенос" }));
    await screen.findByRole("alert");
    expect(screen.queryByText(/Перенос подтверждён/)).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it.each(["blocked", "expired", "empty"])("does not start a %s batch transition", (reason) => {
    const details = { ...batch };
    if (reason === "blocked") details.is_blocked = true;
    if (reason === "expired") details.days_to_expiry = 0;
    if (reason === "empty") details.qty_remaining = "0.000";
    render(view(details));
    expect(screen.getByRole("button", { name: "Проверить пересчёт" })).toBeDisabled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it.each(["tenant_id", "branch_id", "catalog_id", "batch_id"])(
    "rejects preview with another %s",
    async (field) => {
      const response = preview();
      Object.assign(response.source, { [field]: id(20) });
      mocks.post.mockResolvedValue({ data: response });
      render(view());
      fill();
      fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
      await screen.findByRole("alert");
      expect(screen.queryByRole("button", { name: "Сохранить пересчёт" })).not.toBeInTheDocument();
      expect(saveCalls()).toHaveLength(0);
    },
  );

  it("discards an in-flight preview when the cashier session changes", async () => {
    let resolve: (value: { data: ReturnType<typeof preview> }) => void = () => undefined;
    mocks.post.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const rendered = render(view());
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    mocks.user!.id = id(20);
    mocks.generation += 1;
    rendered.rerender(view());
    await act(async () => resolve({ data: preview() }));
    expect(screen.queryByRole("button", { name: "Сохранить пересчёт" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Закрытых упаковок")).toHaveValue("");
    expect(saveCalls()).toHaveLength(0);
  });

  it.each(["readonly", "inventory blocked", "policy unknown", "offline"])(
    "disables writes for %s",
    async (reason) => {
      if (reason === "readonly") mocks.mode = "readonly";
      if (reason === "inventory blocked") mocks.blocked = ["inventory"];
      if (reason === "policy unknown") mocks.policyLoaded = false;
      if (reason === "offline") mocks.online = false;
      render(view());
      expect(screen.getByRole("button", { name: "Проверить пересчёт" })).toBeDisabled();
      expect(screen.queryByRole("button", { name: "Перенести остаток" })).not.toBeInTheDocument();
      expect(mocks.post).not.toHaveBeenCalled();
    },
  );

  it.each(permissions)("does not expose transition without %s", (permission) => {
    mocks.user!.permissions = permissions.filter((p) => p !== permission);
    render(view());
    expect(screen.queryByRole("button", { name: "Проверить пересчёт" })).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it.each(["tenant", "branch permission", "support"])("rejects %s scope", (reason) => {
    if (reason === "tenant") mocks.user!.active_tenant_id = id(20);
    if (reason === "branch permission") mocks.user!.permission_scopes!["incoming.view"] = [id(20)];
    if (reason === "support")
      mocks.user!.support_access = {} as NonNullable<MeResponse["support_access"]>;
    render(view());
    expect(screen.queryByRole("button", { name: "Проверить пересчёт" })).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });
});

describe("explicit opened-pack legacy transition", () => {
  function fillOpened(closed = "0", loose = false) {
    fill();
    fireEvent.change(screen.getByLabelText("Закрытых упаковок"), { target: { value: closed } });
    if (loose)
      fireEvent.change(screen.getByLabelText("Единиц в блистере (необязательно)"), {
        target: { value: "" },
      });
    fireEvent.click(screen.getByRole("button", { name: "Добавить открытую упаковку" }));
    fireEvent.change(screen.getByLabelText("Дата и время вскрытия 1"), {
      target: { value: "2026-10-01T10:00" },
    });
    if (loose)
      fireEvent.change(screen.getByLabelText("Отдельных единиц 1"), { target: { value: "15" } });
    else {
      fireEvent.change(screen.getByLabelText("Целые блистеры 1"), { target: { value: "1" } });
      fireEvent.click(screen.getByRole("button", { name: "Добавить вскрытый блистер" }));
      fireEvent.change(screen.getByLabelText("Номер блистера 1.1"), { target: { value: "2" } });
      fireEvent.change(screen.getByLabelText("Остаток блистера 1.1"), { target: { value: "5" } });
    }
  }
  function acceptOpened(lostSave = false) {
    let saved: ReturnType<typeof record>;
    mocks.post.mockImplementation(
      async (
        path: string,
        payload: LegacyCount & { reconciliation_id: string; command_id: string },
      ) => {
        const response = preview();
        response.source.legacy_quantity = "0.750";
        response.observation = {
          ...response.observation,
          packaging: payload.packaging,
          closed_packs: payload.closed_packs,
          opened_packs: payload.opened_packs,
        };
        response.observed_base_units = 15;
        response.recorded_base_units = "15.000";
        if (path.endsWith("/preview")) return { data: response };
        if (path.endsWith("/records")) {
          saved = { ...record(payload.reconciliation_id), preview: response };
          if (lostSave) throw new Error("connection lost");
          return { data: saved };
        }
        if (path.endsWith("/unit-transition")) return { data: result(payload.command_id) };
        throw new Error("Unexpected request");
      },
    );
    mocks.get.mockImplementation(async () => ({ data: saved }));
  }
  it.each([false, true])(
    "saves partial opened stock with zero closed packs, loose=%s",
    async (loose) => {
      acceptOpened();
      render(view({ ...batch, qty_remaining: "0.750" }));
      fillOpened("0", loose);
      fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Сохранить пересчёт" })).toBeEnabled(),
      );
      const observed = mocks.post.mock.calls[0][1] as LegacyCount;
      expect(observed.closed_packs).toBe(0);
      expect(observed.opened_packs).toHaveLength(1);
      expect(observed.opened_packs[0]).toEqual({
        id: expect.stringMatching(/^[0-9a-f-]{36}$/),
        opened_at: "2026-10-01T10:00:00+05:00",
        intact_blisters: loose ? [] : [1],
        opened_blisters: loose ? [] : [{ number: 2, remaining_units: 5 }],
        loose_units: loose ? 15 : 0,
      });
      fireEvent.click(screen.getByRole("button", { name: "Сохранить пересчёт" }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Перенести остаток" })).toBeEnabled(),
      );
      expect((saveCalls()[0][1] as LegacyCount).opened_packs).toEqual(observed.opened_packs);
      expect(transferCalls()).toHaveLength(0);
      fireEvent.click(
        within(openConfirmation()).getByRole("button", { name: "Подтвердить перенос" }),
      );
      await screen.findByText(/Перенос подтверждён/);
      expect(transferCalls()).toHaveLength(1);
    },
  );
  it.each([
    ["Целые блистеры 1", "1,1"],
    ["Целые блистеры 1", "3"],
    ["Номер блистера 1.1", "1"],
    ["Остаток блистера 1.1", "10"],
    ["Остаток блистера 1.1", "0"],
    ["Дата и время вскрытия 1", ""],
  ])("rejects invalid observed composition %s=%s", async (label, value) => {
    render(view());
    fillOpened();
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
    await screen.findByRole("alert");
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("recovers the exact opened observation after a lost save response without resaving", async () => {
    acceptOpened(true);
    render(view({ ...batch, qty_remaining: "0.750" }));
    fillOpened();
    fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Сохранить пересчёт" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Сохранить пересчёт" }));
    await screen.findByRole("alert");
    expect(screen.queryByLabelText("Дата и время вскрытия 1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Проверить сохранение" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Перенести остаток" })).toBeEnabled(),
    );
    expect(saveCalls()).toHaveLength(1);
    expect(transferCalls()).toHaveLength(0);
  });
  it("invalidates an opened preview when its blister count changes", async () => {
    acceptOpened();
    render(view());
    fillOpened();
    fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Сохранить пересчёт" })).toBeEnabled(),
    );
    fireEvent.change(screen.getByLabelText("Остаток блистера 1.1"), { target: { value: "4" } });
    expect(screen.queryByRole("button", { name: "Сохранить пересчёт" })).not.toBeInTheDocument();
    expect(saveCalls()).toHaveLength(0);
  });
  it("keeps the second pack and its blister identity after deleting the first", async () => {
    acceptOpened();
    render(view());
    fillOpened();
    fireEvent.click(screen.getByRole("button", { name: "Добавить открытую упаковку" }));
    fireEvent.change(screen.getByLabelText("Дата и время вскрытия 2"), {
      target: { value: "2026-10-02T11:00:00" },
    });
    fireEvent.change(screen.getByLabelText("Целые блистеры 2"), { target: { value: "2" } });
    const second = screen.getByRole("group", { name: "Открытая упаковка 2" });
    fireEvent.click(within(second).getByRole("button", { name: "Добавить вскрытый блистер" }));
    fireEvent.change(screen.getByLabelText("Номер блистера 2.1"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Остаток блистера 2.1"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    const secondId = (mocks.post.mock.calls[0][1] as LegacyCount).opened_packs[1].id;
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Проверить пересчёт" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Удалить открытую упаковку 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    expect((mocks.post.mock.calls[1][1] as LegacyCount).opened_packs).toEqual([
      {
        id: secondId,
        opened_at: "2026-10-02T11:00:00+05:00",
        intact_blisters: [2],
        opened_blisters: [{ number: 1, remaining_units: 3 }],
        loose_units: 0,
      },
    ]);
  });
  it("removes nested blister values and adds a fresh row without reviving deleted remainders", async () => {
    acceptOpened();
    render(view());
    fillOpened();
    fireEvent.change(screen.getByLabelText("Целые блистеры 1"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить вскрытый блистер" }));
    fireEvent.change(screen.getByLabelText("Номер блистера 1.2"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Остаток блистера 1.2"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Удалить блистер 1.1" }));
    expect(screen.getByLabelText("Номер блистера 1.1")).toHaveValue("1");
    expect(screen.getByLabelText("Остаток блистера 1.1")).toHaveValue("7");
    fireEvent.click(screen.getByRole("button", { name: "Добавить вскрытый блистер" }));
    expect(screen.getByLabelText("Номер блистера 1.2")).toHaveValue("");
    expect(screen.getByLabelText("Остаток блистера 1.2")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("Номер блистера 1.2"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Остаток блистера 1.2"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect((mocks.post.mock.calls[0][1] as LegacyCount).opened_packs[0].opened_blisters).toEqual([
      { number: 1, remaining_units: 7 },
      { number: 2, remaining_units: 4 },
    ]);
  });
  it("creates a fresh pack identity and empty composition after removing and readding the last pack", async () => {
    acceptOpened();
    render(view());
    fillOpened();
    fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    const oldId = (mocks.post.mock.calls[0][1] as LegacyCount).opened_packs[0].id;
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Проверить пересчёт" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Удалить открытую упаковку 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Добавить открытую упаковку" }));
    expect(screen.getByLabelText("Дата и время вскрытия 1")).toHaveValue("");
    expect(screen.getByLabelText("Целые блистеры 1")).toHaveValue("");
    expect(screen.queryByLabelText("Номер блистера 1.1")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Дата и время вскрытия 1"), {
      target: { value: "2026-10-02T11:00:00" },
    });
    fireEvent.change(screen.getByLabelText("Целые блистеры 1"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    const current = (mocks.post.mock.calls[1][1] as LegacyCount).opened_packs;
    expect(current).toHaveLength(1);
    expect(current[0].id).not.toBe(oldId);
    expect(current[0]).toMatchObject({
      opened_at: "2026-10-02T11:00:00+05:00",
      intact_blisters: [2],
      opened_blisters: [],
      loose_units: 0,
    });
  });
  it("requires a new explicit no-open declaration after removing the last opened pack", async () => {
    render(view());
    fillOpened("1");
    fireEvent.click(screen.getByRole("button", { name: "Удалить открытую упаковку 1" }));
    expect(screen.getByLabelText("Подтверждаю: открытых упаковок нет")).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Проверить пересчёт" }));
    await screen.findByRole("alert");
    expect(mocks.post).not.toHaveBeenCalled();
  });
});
