import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MeResponse } from "@/features/auth/types";
import type {
  PhysicalWriteoffInput,
  PhysicalWriteoffResult,
} from "@/features/inventory/physicalWriteoff";
import type { PendingPhysicalWriteoff } from "@/features/inventory/physicalWriteoffRecovery";
import { PhysicalWriteOffWorkspace } from "@/features/inventory/PhysicalWriteOffWorkspace";
import type { PhysicalLot } from "@/features/inventory/physicalLots";

const mocks = vi.hoisted(() => ({
  user: null as MeResponse | null,
  online: true,
  generation: 0,
  load: vi.fn(),
  run: vi.fn(),
  inspect: vi.fn(),
  acknowledge: vi.fn(),
  cancel: vi.fn(),
}));
vi.mock("@/features/auth/hooks", () => ({ useAuth: () => ({ user: mocks.user }) }));
vi.mock("@/lib/connectivityContext", () => ({
  useConnectivity: () => ({ canUseServer: mocks.online }),
}));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: () => {
    const generation = mocks.generation;
    return () => generation === mocks.generation;
  },
}));
vi.mock("@/features/inventory/physicalWriteoffRecovery", () => ({
  loadPhysicalWriteoff: (...args: unknown[]) => mocks.load(...args),
  runPhysicalWriteoff: (...args: unknown[]) => mocks.run(...args),
  inspectPhysicalWriteoff: (...args: unknown[]) => mocks.inspect(...args),
  requestPhysicalWriteoffCancellation: (...args: unknown[]) => mocks.cancel(...args),
  acknowledgePhysicalWriteoff: (...args: unknown[]) => mocks.acknowledge(...args),
}));
vi.mock("@/features/inventory/PhysicalWriteOffModal", () => ({
  PhysicalWriteOffModal: ({
    writable,
    onSubmit,
  }: {
    writable: boolean;
    onSubmit: (input: PhysicalWriteoffInput, total: string) => Promise<void>;
  }) => (
    <button
      disabled={!writable}
      onClick={() => {
        void onSubmit(input(), "20");
      }}
    >
      Подтвердить выбор
    </button>
  ),
}));

const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const lot: PhysicalLot = {
  lot_id: id(1),
  branch_id: id(2),
  catalog_id: id(3),
  catalog_name: "Препарат",
  expires_at: "2027-01-01",
  closed_packs: "2",
  opened_units: "0",
  base_units: "40",
  is_blocked: false,
  blocking_revision: "0",
  packaging: {
    version: 2,
    unit: "tablet",
    units_per_pack: 20,
    units_per_blister: 10,
    dispense_step: 1,
  },
};
function input(): PhysicalWriteoffInput {
  return {
    branch_id: id(2),
    lot_id: id(1),
    reason: "damaged",
    comment: null,
    selection: { closed_packs: 1, opened_packs: [] },
  };
}
function pending(): PendingPhysicalWriteoff {
  return {
    version: 1,
    tenantId: id(5),
    userId: id(6),
    branchId: id(2),
    request: { ...input(), operation_id: id(7) },
    expectedBaseUnits: "20",
  };
}
function result(): PhysicalWriteoffResult {
  return {
    operation_id: id(7),
    branch_id: id(2),
    lot_id: id(1),
    reason: "damaged",
    comment: null,
    base_units: "20",
    closed_packs: "1",
    created_at: "2026-10-17T10:00:00Z",
  };
}
let client: QueryClient;
let saved: PendingPhysicalWriteoff | null;
const close = vi.fn();
const barrier = vi.fn();
function view(writable = true, selected: PhysicalLot | null = lot) {
  return (
    <QueryClientProvider client={client}>
      <PhysicalWriteOffWorkspace
        lot={selected}
        writable={writable}
        onClose={close}
        onBarrier={barrier}
      />
    </QueryClientProvider>
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.online = true;
  mocks.generation = 0;
  saved = null;
  mocks.user = {
    id: id(6),
    active_tenant_id: id(5),
    is_developer: false,
    support_access: null,
    permissions: ["batches.view", "batches.write_off"],
    permission_scopes: { "batches.view": [id(2)], "batches.write_off": [id(2)] },
  } as MeResponse;
  mocks.load.mockImplementation(() => saved);
  mocks.run.mockImplementation(async () => {
    saved = pending();
    return result();
  });
  mocks.inspect.mockResolvedValue(result());
  mocks.acknowledge.mockImplementation(async () => {
    saved = null;
  });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
});
afterEach(() => {
  cleanup();
  client.clear();
});

describe("physical write-off recovery workspace", () => {
  it("does not label a different persisted command with an old successful result", async () => {
    saved = pending();
    render(view());
    fireEvent.click(await screen.findByRole("button", { name: "Проверить результат" }));
    await screen.findByRole("status");
    saved = { ...pending(), request: { ...pending().request, operation_id: id(9) } };
    fireEvent(window, new Event("storage"));
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
    expect(screen.getByText(`Списание: ${id(9)}`)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Готово" })).not.toBeInTheDocument();
    expect(barrier).toHaveBeenLastCalledWith(true);
    expect(mocks.inspect).toHaveBeenCalledTimes(1);
    expect(mocks.run).not.toHaveBeenCalled();
  });

  it("does not post or inspect on mount, focus or pending refresh", async () => {
    saved = pending();
    render(view());
    expect(await screen.findByRole("region", { name: "Результат списания" })).toBeInTheDocument();
    fireEvent(window, new Event("focus"));
    fireEvent(window, new Event("storage"));
    expect(mocks.run).not.toHaveBeenCalled();
    expect(mocks.inspect).not.toHaveBeenCalled();
    expect(barrier).toHaveBeenLastCalledWith(true);
    expect(screen.queryByRole("button", { name: "Подтвердить выбор" })).not.toBeInTheDocument();
  });

  it("keeps a lost response pending across remount and recovers only by explicit inspection", async () => {
    mocks.run.mockImplementation(async () => {
      saved = pending();
      throw new Error("Ответ потерян");
    });
    const first = render(view());
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить выбор" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Результат не подтверждён");
    expect(barrier).toHaveBeenLastCalledWith(true);
    first.unmount();
    render(view());
    await screen.findByRole("button", { name: "Проверить результат" });
    expect(mocks.run).toHaveBeenCalledTimes(1);
    expect(mocks.inspect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Проверить результат" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Списание выполнено");
    expect(mocks.run).toHaveBeenCalledTimes(1);
    expect(saved?.request.operation_id).toBe(id(7));
    fireEvent.click(screen.getByRole("button", { name: "Готово" }));
    await waitFor(() => expect(barrier).toHaveBeenLastCalledWith(false));
    expect(saved).toBeNull();
  });

  it("allows readonly status inspection while preventing mutation retry", async () => {
    saved = pending();
    render(view(false));
    expect(
      await screen.findByRole("button", { name: "Повторить сохранённое списание" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Проверить результат" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Проверить результат" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Списание выполнено");
    expect(mocks.run).not.toHaveBeenCalled();
  });

  it("keeps the barrier after an unconfirmed status and only retries the stored command explicitly", async () => {
    saved = pending();
    mocks.inspect.mockResolvedValue(null);
    render(view());
    fireEvent.click(await screen.findByRole("button", { name: "Проверить результат" }));
    await waitFor(() => expect(mocks.inspect).toHaveBeenCalledTimes(1));
    expect(mocks.run).not.toHaveBeenCalled();
    expect(barrier).toHaveBeenLastCalledWith(true);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Повторить сохранённое списание" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Повторить сохранённое списание" }));
    await screen.findByRole("status");
    expect(mocks.run).toHaveBeenCalledWith({ tenantId: id(5), userId: id(6), branchId: id(2) });
  });

  it("blocks new work when storage is unreadable and recovers only after a successful read", async () => {
    mocks.load.mockImplementation(() => {
      throw new Error("Хранилище недоступно");
    });
    render(view());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось прочитать сохранённое списание",
    );
    expect(barrier).toHaveBeenLastCalledWith(true);
    expect(screen.queryByRole("button", { name: "Подтвердить выбор" })).not.toBeInTheDocument();
    mocks.load.mockReturnValue(null);
    fireEvent.click(screen.getByRole("button", { name: "Проверить хранилище" }));
    expect(await screen.findByRole("button", { name: "Подтвердить выбор" })).toBeEnabled();
    expect(barrier).toHaveBeenLastCalledWith(false);
    expect(mocks.run).not.toHaveBeenCalled();
  });

  it("ignores a late result after auth generation changes", async () => {
    saved = pending();
    let finish!: (value: PhysicalWriteoffResult) => void;
    mocks.inspect.mockReturnValue(
      new Promise<PhysicalWriteoffResult>((resolve) => {
        finish = resolve;
      }),
    );
    const invalidate = vi.spyOn(client, "invalidateQueries");
    render(view());
    fireEvent.click(await screen.findByRole("button", { name: "Проверить результат" }));
    mocks.generation += 1;
    await act(async () => {
      finish(result());
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(invalidate).not.toHaveBeenCalled();
    expect(saved).not.toBeNull();
    expect(mocks.acknowledge).not.toHaveBeenCalled();
  });

  it("does not contact the server offline or after branch permission is removed", async () => {
    saved = pending();
    mocks.online = false;
    const rendered = render(view());
    expect(await screen.findByRole("button", { name: "Проверить результат" })).toBeDisabled();
    mocks.online = true;
    mocks.user = { ...mocks.user!, permissions: ["batches.view"] };
    rendered.rerender(view());
    expect(screen.getByRole("button", { name: "Проверить результат" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Повторить сохранённое списание" })).toBeDisabled();
    expect(mocks.run).not.toHaveBeenCalled();
    expect(mocks.inspect).not.toHaveBeenCalled();
  });
});

describe("write-off cancellation controls", () => {
  it("requires explicit confirmation, shows cancelled separately and acknowledges it", async () => {
    saved = pending();
    mocks.cancel.mockResolvedValue({
      ...saved.request,
      status: "cancelled",
      cancelled_at: "2026-10-16T10:00:00Z",
    });
    render(view());
    fireEvent.click(
      await screen.findByRole("button", { name: "Отменить неподтверждённое списание" }),
    );
    expect(mocks.cancel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить отмену списания" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Списание отменено. Остатки не изменены.",
    );
    expect(screen.queryByText(/Списано:/)).not.toBeInTheDocument();
    expect(mocks.run).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Готово" }));
    await waitFor(() => expect(barrier).toHaveBeenLastCalledWith(false));
  });
  it("after reload with cancellation intent offers only status or cancellation retry", async () => {
    saved = { ...pending(), cancelRequested: true };
    mocks.cancel.mockResolvedValue(result());
    render(view());
    expect(await screen.findByRole("button", { name: "Повторить отмену списания" })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Повторить сохранённое списание" }),
    ).not.toBeInTheDocument();
    expect(mocks.cancel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Повторить отмену списания" }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить отмену списания" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Списание выполнено.");
    expect(screen.getByText("Списано: 20 базовых единиц.")).toBeInTheDocument();
  });
  it("disables cancellation in readonly mode", async () => {
    saved = pending();
    render(view(false));
    expect(
      await screen.findByRole("button", { name: "Отменить неподтверждённое списание" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Проверить результат" })).toBeEnabled();
  });
});
