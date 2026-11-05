import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  reviewedMixedDispositionTarget,
  mixedDispositionTarget,
  type ResolvedMixedDisposition,
} from "@/features/customerReturns/mixedDisposition";
import { MixedDispositionHistoryPanel } from "@/features/customerReturns/MixedDispositionHistoryPanel";
import { CustomerReturnsPanel } from "@/features/customerReturns/CustomerReturnsPanel";
import { MixedDispositionModal } from "@/features/customerReturns/MixedDispositionModal";
import { MixedDispositionWorkspace } from "@/features/customerReturns/MixedDispositionWorkspace";
import type { PendingMixedDisposition } from "@/features/customerReturns/mixedDispositionRecovery";
import type { MixedCustomerReturnItem } from "@/features/customerReturns/types";

const mocks = vi.hoisted(() => ({
  load: vi.fn(),
  inspect: vi.fn(),
  run: vi.fn(),
  acknowledge: vi.fn(),
  history: vi.fn(),
  queue: vi.fn(),
  permissions: [] as string[],
  permissionScopes: {} as Record<string, string[] | null>,
  mode: "full",
  blockedModules: [] as string[],
  policyLoaded: true,
  support: false,
  generation: 0,
  userId: "00000000-0000-4000-8000-000000000002",
  tenantId: "00000000-0000-4000-8000-000000000001",
}));
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      id: mocks.userId,
      active_tenant_id: mocks.tenantId,
      is_developer: false,
      support_access: mocks.support ? {} : null,
      permissions: mocks.permissions,
      permission_scopes: mocks.permissionScopes,
    },
  }),
}));
vi.mock("@/features/tenantAccess/queries", () => ({
  useTenantAccessPolicy: () => ({
    data: mocks.policyLoaded
      ? {
          tenant_id: mocks.tenantId,
          mode: mocks.mode,
          blocked_modules: mocks.blockedModules,
        }
      : undefined,
  }),
}));
vi.mock("@/features/pos/posStorageScope", async (original) => ({
  ...(await original<typeof import("@/features/pos/posStorageScope")>()),
  capturePosSessionGuard: () => {
    const generation = mocks.generation;
    return () => generation === mocks.generation;
  },
}));
vi.mock("@/features/customerReturns/mixedDispositionRecovery", async (original) => ({
  ...(await original<typeof import("@/features/customerReturns/mixedDispositionRecovery")>()),
  loadMixedDisposition: mocks.load,
  inspectMixedDisposition: mocks.inspect,
  runMixedDisposition: mocks.run,
  acknowledgeMixedDisposition: mocks.acknowledge,
}));
vi.mock("@/features/customerReturns/mixedDisposition", async (original) => ({
  ...(await original<typeof import("@/features/customerReturns/mixedDisposition")>()),
  listMixedDispositionHistory: mocks.history,
}));
vi.mock("@/features/customerReturns/api", async (original) => ({
  ...(await original<typeof import("@/features/customerReturns/api")>()),
  listCustomerReturns: mocks.queue,
  listMixedCustomerReturns: mocks.queue,
  listPhysicalCustomerReturns: mocks.queue,
}));
vi.mock("@/features/foundation/queries", () => ({
  useBranchesQuery: () => ({
    data: [
      {
        id: "00000000-0000-4000-8000-000000000003",
        name: "Аптека Рудаки",
      },
    ],
  }),
}));
const uuid = (value: number): string =>
  `00000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const scope = { tenantId: uuid(1), userId: uuid(2) };
const operationScope = { ...scope, branchId: uuid(3) };
const item: MixedCustomerReturnItem = {
  inventory_kind: "legacy_pack",
  operation_id: uuid(4),
  line_id: uuid(5),
  batch_id: uuid(6),
  lot_id: null,
  legacy_quantity: "0.125",
  base_units: null,
  unit_label: "уп.",
  branch_id: uuid(3),
  branch_name: "Аптека Рудаки",
  report_timezone: "Asia/Dushanbe",
  parent_sale_id: uuid(7),
  parent_receipt_number: "S-001",
  return_sale_id: null,
  return_receipt_number: null,
  catalog_id: uuid(8),
  catalog_name: "Парацетамол",
  batch_number: "LOT-2026",
  expires_at: "2027-06-30",
  received_at: "2026-10-12T10:00:00Z",
  refund_reason: "quality_issue",
  refund_comment: "Первичный возврат",
  intake_kind: "zero_return",
  status: "pending",
};
const physical: MixedCustomerReturnItem = {
  ...item,
  inventory_kind: "physical_unit",
  batch_id: null,
  legacy_quantity: null,
  lot_id: uuid(9),
  base_units: 2,
  unit_label: "капс.",
};
function saved(row = item): PendingMixedDisposition {
  const reviewedTarget = reviewedMixedDispositionTarget(row);
  return {
    version: 1,
    ...operationScope,
    reviewedTarget,
    request: {
      decision_operation_id: uuid(10),
      target: mixedDispositionTarget(reviewedTarget),
      disposition_type: "disposed",
      reason_code: "damaged",
      comment: "Сохранённый комментарий",
    },
  };
}
function resolved(row = item): ResolvedMixedDisposition {
  const pending = saved(row);
  return {
    version: 1,
    document_kind: "mixed_customer_return_disposition",
    state: "resolved",
    decision_operation_id: pending.request.decision_operation_id,
    tenant_id: scope.tenantId,
    branch_id: row.branch_id,
    parent_sale_id: row.parent_sale_id,
    intake_kind: row.intake_kind,
    target: pending.reviewedTarget,
    disposition_type: "disposed",
    reason_code: "damaged",
    comment: pending.request.comment,
    request_hash: "a".repeat(64),
    actor_id: uuid(11),
    resolved_at: "2026-10-12T10:00:00Z",
  };
}
function provider(children: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
function modal(
  row: MixedCustomerReturnItem | null = item,
  pending: PendingMixedDisposition | null = null,
) {
  const onClose = vi.fn();
  const onResolved = vi.fn();
  const view = render(
    <MixedDispositionModal
      scope={scope}
      item={row}
      pending={pending}
      onClose={onClose}
      onResolved={onResolved}
    />,
  );
  return { ...view, onClose, onResolved };
}
function fillForm(confirmed = true) {
  fireEvent.click(screen.getByRole("radio", { name: "Утилизировано" }));
  fireEvent.change(screen.getByLabelText("Причина решения"), { target: { value: "damaged" } });
  fireEvent.change(screen.getByLabelText("Комментарий к решению"), {
    target: { value: "  Фактически выполнено  " },
  });
  if (confirmed)
    fireEvent.click(screen.getByRole("checkbox", { name: /Подтверждаю, что действие/ }));
}
function workspace() {
  return render(
    provider(
      <MixedDispositionWorkspace>
        {({ onResolve, canResolve, blocked }) => (
          <>
            <p>Барьер: {blocked ? "закрыт" : "открыт"}</p>
            <button disabled={blocked || !canResolve(item)} onClick={() => onResolve(item)}>
              Решить строку
            </button>
            <button onClick={() => onResolve(item)}>Проверить актуальный барьер</button>
          </>
        )}
      </MixedDispositionWorkspace>,
    ),
  );
}
afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  mocks.permissions = ["customer_returns.view", "customer_returns.resolve", "branches.view"];
  mocks.permissionScopes = {
    "customer_returns.view": null,
    "customer_returns.resolve": [uuid(3)],
    "branches.view": null,
  };
  mocks.mode = "full";
  mocks.policyLoaded = true;
  mocks.blockedModules = [];
  mocks.support = false;
  mocks.generation = 0;
  mocks.userId = scope.userId;
  mocks.tenantId = scope.tenantId;
  mocks.load.mockReturnValue(null);
  mocks.inspect.mockResolvedValue({
    state: "not_found",
    decision_operation_id: uuid(10),
    branch_id: uuid(3),
  });
  mocks.run.mockResolvedValue(resolved());
  mocks.acknowledge.mockResolvedValue(true);
  mocks.history.mockResolvedValue({
    items: [resolved()],
    total: 1,
    page: 1,
    page_size: 25,
    report_timezone: "Asia/Dushanbe",
  });
});
describe("mixed disposition confirmation form", () => {
  it("requires explicit whole-row confirmation and sends exact normalized input and minimal reviewed snapshot", async () => {
    const view = modal();
    expect(screen.getByText(/Весь принятый объём: 0,125 уп\./)).toBeInTheDocument();
    expect(mocks.inspect).not.toHaveBeenCalled();
    fillForm(false);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить решение" }));
    await screen.findByText("Подтвердите фактическое выполнение");
    expect(mocks.run).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: /Подтверждаю, что действие/ }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить решение" }));
    await screen.findByText("Решение сохранено");
    expect(mocks.run).toHaveBeenCalledExactlyOnceWith(
      operationScope,
      {
        target: {
          inventory_kind: "legacy_pack",
          intake_operation_id: item.operation_id,
          line_id: item.line_id,
          batch_id: item.batch_id,
        },
        disposition_type: "disposed",
        reason_code: "damaged",
        comment: "Фактически выполнено",
      },
      reviewedMixedDispositionTarget(item),
    );
    expect(view.onResolved).toHaveBeenCalledOnce();
    expect(view.onClose).not.toHaveBeenCalled();
    expect(mocks.acknowledge).not.toHaveBeenCalled();
  });
  it("requires an action and reason before saving", async () => {
    modal();
    fireEvent.click(screen.getByRole("checkbox", { name: /Подтверждаю, что действие/ }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить решение" }));
    expect(await screen.findAllByRole("alert")).toHaveLength(2);
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("uses physical whole-unit snapshot while the request contains only target identity", async () => {
    mocks.run.mockResolvedValue(resolved(physical));
    modal(physical);
    expect(screen.getByText(/Весь принятый объём: 2 капс\./)).toBeInTheDocument();
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить решение" }));
    await screen.findByText("Решение сохранено");
    expect(mocks.run).toHaveBeenCalledExactlyOnceWith(
      operationScope,
      expect.objectContaining({
        target: {
          inventory_kind: "physical_unit",
          intake_operation_id: physical.operation_id,
          line_id: physical.line_id,
          lot_id: physical.lot_id,
        },
      }),
      reviewedMixedDispositionTarget(physical),
    );
    expect(mocks.run.mock.calls[0]?.[1].target).not.toHaveProperty("base_units");
    expect(mocks.run.mock.calls[0]?.[1]).not.toHaveProperty("shift_id");
  });
  it.each(["readonly", "loading", "blocked-pos", "support", "outside-branch"])(
    "disables a new disposition when access is %s",
    (condition) => {
      if (condition === "readonly") mocks.mode = "readonly";
      if (condition === "loading") mocks.policyLoaded = false;
      if (condition === "blocked-pos") mocks.blockedModules = ["pos"];
      if (condition === "support") mocks.support = true;
      if (condition === "outside-branch")
        mocks.permissionScopes["customer_returns.resolve"] = [uuid(90)];
      modal();
      expect(screen.getByRole("button", { name: "Сохранить решение" })).toBeDisabled();
      expect(screen.getByRole("radio", { name: "Утилизировано" })).toBeDisabled();
      expect(mocks.run).not.toHaveBeenCalled();
    },
  );
});
describe("mixed disposition modal recovery", () => {
  it("mount only inspects saved intent; not_found requires explicit retry with no replacement input", async () => {
    mocks.load.mockReturnValue(saved());
    modal(null, saved());
    await screen.findByText("Решение с этим номером пока не найдено.");
    expect(mocks.inspect).toHaveBeenCalledExactlyOnceWith(operationScope);
    expect(mocks.run).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Сохранить решение" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить сохранённый запрос" }));
    await screen.findByText("Решение сохранено");
    expect(mocks.run).toHaveBeenCalledExactlyOnceWith(operationScope);
  });
  it("allows readonly inspection and acknowledgment, while not_found cannot be retried", async () => {
    mocks.mode = "readonly";
    mocks.load.mockReturnValue(saved());
    const view = modal(null, saved());
    await screen.findByText("Решение с этим номером пока не найдено.");
    expect(screen.getByRole("button", { name: "Повторить сохранённый запрос" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Проверить результат" })).toBeEnabled();
    mocks.inspect.mockResolvedValue(resolved());
    fireEvent.click(screen.getByRole("button", { name: "Проверить результат" }));
    await screen.findByText("Решение сохранено");
    expect(mocks.run).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Завершить проверку" }));
    await waitFor(() => expect(view.onClose).toHaveBeenCalledOnce());
    expect(mocks.acknowledge).toHaveBeenCalledExactlyOnceWith(operationScope, resolved());
  });
  it("does not clear or close a server result when fresh acknowledgment fails", async () => {
    mocks.load.mockReturnValue(saved());
    mocks.inspect.mockResolvedValue(resolved());
    mocks.acknowledge.mockResolvedValue(false);
    const view = modal(null, saved());
    await screen.findByText("Решение сохранено");
    fireEvent.click(screen.getByRole("button", { name: "Завершить проверку" }));
    await screen.findByText("Результат не подтверждён. Сохранённый запрос не удалён.");
    expect(view.onClose).not.toHaveBeenCalled();
    expect(view.onResolved).not.toHaveBeenCalled();
  });
  it("a lost explicit submission transitions into recovery without submitting again", async () => {
    mocks.run.mockImplementationOnce(async () => {
      mocks.load.mockReturnValue(saved());
      throw new Error("Ответ потерян. Проверьте результат.");
    });
    modal();
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить решение" }));
    await screen.findByText("Ответ потерян. Проверьте результат.");
    expect(screen.getByRole("button", { name: "Проверить результат" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Сохранить решение" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Повторить сохранённый запрос" }),
    ).not.toBeInTheDocument();
    expect(mocks.inspect).not.toHaveBeenCalled();
    expect(mocks.run).toHaveBeenCalledOnce();
  });
  it("lookup failure never enables retry", async () => {
    mocks.load.mockReturnValue(saved());
    mocks.inspect.mockRejectedValue(new Error("Не удалось проверить статус"));
    modal(null, saved());
    await screen.findByText("Не удалось проверить статус");
    expect(
      screen.queryByRole("button", { name: "Повторить сохранённый запрос" }),
    ).not.toBeInTheDocument();
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("ignores delayed submission after session change", async () => {
    let finish: ((value: ResolvedMixedDisposition) => void) | undefined;
    mocks.run.mockImplementationOnce(
      () =>
        new Promise<ResolvedMixedDisposition>((resolve) => {
          finish = resolve;
        }),
    );
    const view = modal();
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить решение" }));
    await waitFor(() => expect(mocks.run).toHaveBeenCalledOnce());
    mocks.generation += 1;
    await act(async () => {
      finish?.(resolved());
    });
    expect(view.onResolved).not.toHaveBeenCalled();
    expect(view.onClose).not.toHaveBeenCalled();
    expect(screen.queryByText("Решение сохранено")).not.toBeInTheDocument();
  });
});
describe("mixed disposition workspace barrier", () => {
  it("uses the saved branch for recovery even when the visible queue row belongs elsewhere", async () => {
    const pending = { ...saved(), branchId: uuid(90) };
    mocks.load.mockReturnValue(pending);
    mocks.permissionScopes["customer_returns.resolve"] = [uuid(3), uuid(90)];
    mocks.inspect.mockResolvedValue({
      state: "not_found",
      decision_operation_id: pending.request.decision_operation_id,
      branch_id: uuid(90),
    });
    workspace();
    expect(screen.getByRole("button", { name: "Решить строку" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Проверить сохранённое решение" }));
    await screen.findByText("Решение с этим номером пока не найдено.");
    expect(mocks.inspect).toHaveBeenCalledExactlyOnceWith({ ...scope, branchId: uuid(90) });
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("resolve-only access restores saved intent without fetching a queue or history", async () => {
    mocks.permissions = ["customer_returns.resolve"];
    mocks.load.mockReturnValue(saved());
    render(provider(<CustomerReturnsPanel />));
    expect(screen.queryByRole("group", { name: "Вид возвратов" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "История решений" })).not.toBeInTheDocument();
    expect(mocks.queue).not.toHaveBeenCalled();
    expect(mocks.history).not.toHaveBeenCalled();
    expect(mocks.inspect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Проверить сохранённое решение" }));
    await screen.findByText("Решение с этим номером пока не найдено.");
    expect(mocks.inspect).toHaveBeenCalledExactlyOnceWith(operationScope);
    expect(mocks.queue).not.toHaveBeenCalled();
    expect(mocks.history).not.toHaveBeenCalled();
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("restores pending without a queue row and opens only explicit status inspection", async () => {
    mocks.load.mockReturnValue(saved());
    workspace();
    expect(screen.getByRole("button", { name: "Решить строку" })).toBeDisabled();
    expect(mocks.inspect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Проверить сохранённое решение" }));
    await screen.findByText("Решение с этим номером пока не найдено.");
    expect(mocks.inspect).toHaveBeenCalledExactlyOnceWith(operationScope);
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("rechecks storage at action time and responds to cross-tab storage events", async () => {
    workspace();
    expect(screen.getByText("Барьер: открыт")).toBeInTheDocument();
    mocks.load.mockReturnValue(saved());
    fireEvent.click(screen.getByRole("button", { name: "Проверить актуальный барьер" }));
    expect(screen.getByText("Барьер: закрыт")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    mocks.load.mockReturnValue(null);
    fireEvent(window, new StorageEvent("storage"));
    expect(screen.getByText("Барьер: открыт")).toBeInTheDocument();
    mocks.load.mockReturnValue(saved());
    fireEvent.focus(window);
    expect(screen.getByText("Барьер: закрыт")).toBeInTheDocument();
  });
  it("corrupted storage blocks new actions instead of dropping the unknown decision", () => {
    mocks.load.mockImplementation(() => {
      throw new Error("corrupt");
    });
    workspace();
    expect(screen.getByRole("alert")).toHaveTextContent("Новые решения заблокированы");
    expect(screen.getByRole("button", { name: "Решить строку" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Проверить актуальный барьер" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mocks.run).not.toHaveBeenCalled();
  });
});
describe("mixed disposition read-only history", () => {
  it.each(["view-revoked", "account-changed", "support"])(
    "hides already cached history when access becomes %s",
    async (condition) => {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
      const tree = () => (
        <QueryClientProvider client={client}>
          <MixedDispositionHistoryPanel scope={scope} />
        </QueryClientProvider>
      );
      const view = render(tree());
      await screen.findByRole("article", { name: "Сохранённое решение по карантину" });
      if (condition === "view-revoked") mocks.permissions = ["customer_returns.resolve"];
      if (condition === "account-changed") mocks.userId = uuid(90);
      if (condition === "support") mocks.support = true;
      view.rerender(tree());
      expect(screen.queryByRole("article")).not.toBeInTheDocument();
      expect(screen.queryByText("Сохранённый комментарий")).not.toBeInTheDocument();
      expect(mocks.history).toHaveBeenCalledOnce();
    },
  );
  it("shows immutable quantity, original reason and pharmacy timezone without mutation actions", async () => {
    render(provider(<MixedDispositionHistoryPanel scope={scope} />));
    const article = await screen.findByRole("article", {
      name: "Сохранённое решение по карантину",
    });
    expect(within(article).getByText("Обычные пачки: 0,125 уп.")).toBeInTheDocument();
    expect(within(article).getByText("Приём без выплаты")).toBeInTheDocument();
    expect(within(article).getByText("Повреждение")).toBeInTheDocument();
    expect(within(article).getByText("Сохранённый комментарий")).toBeInTheDocument();
    expect(within(article).getByText(/15:00/)).toHaveAttribute("datetime", "2026-10-12T10:00:00Z");
    expect(mocks.history).toHaveBeenCalledExactlyOnceWith(
      { branch_id: undefined, page: 1, page_size: 25 },
      scope,
    );
    expect(
      screen.queryByRole("button", { name: /Сохранить|Повторить сохранённый/ }),
    ).not.toBeInTheDocument();
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("paginates separately and resets to first page when branch changes", async () => {
    mocks.history.mockResolvedValue({
      items: [resolved()],
      total: 26,
      page: 1,
      page_size: 25,
      report_timezone: "Asia/Dushanbe",
    });
    render(provider(<MixedDispositionHistoryPanel scope={scope} />));
    await screen.findByRole("article");
    fireEvent.click(screen.getByRole("button", { name: "Вперёд" }));
    await waitFor(() =>
      expect(mocks.history).toHaveBeenLastCalledWith(
        { branch_id: undefined, page: 2, page_size: 25 },
        scope,
      ),
    );
    fireEvent.change(screen.getByLabelText("Точка истории"), { target: { value: uuid(3) } });
    await waitFor(() =>
      expect(mocks.history).toHaveBeenLastCalledWith(
        { branch_id: uuid(3), page: 1, page_size: 25 },
        scope,
      ),
    );
  });
  it("handles history errors and empty scoped results using GET retry only", async () => {
    mocks.history.mockRejectedValueOnce(new Error("offline"));
    render(provider(<MixedDispositionHistoryPanel scope={scope} />));
    await screen.findByText("Не удалось загрузить историю решений");
    mocks.history.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      report_timezone: null,
    });
    fireEvent.click(screen.getByRole("button", { name: "Повторить загрузку истории" }));
    await screen.findByText("Решений пока нет");
    expect(mocks.history).toHaveBeenCalledTimes(2);
    expect(mocks.run).not.toHaveBeenCalled();
  });
});
