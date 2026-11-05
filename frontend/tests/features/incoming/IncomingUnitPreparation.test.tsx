import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({
  update: vi.fn(),
  saveDraft: vi.fn(),
  commitDraft: vi.fn(),
  listDrafts: vi.fn(),
  get: vi.fn(),
}));

vi.mock("@/features/incoming/api", () => ({
  updateIncomingItem: (...args: unknown[]) => calls.update(...args),
  saveIncomingDraftRow: (...args: unknown[]) => calls.saveDraft(...args),
  commitIncomingDraftRow: (...args: unknown[]) => calls.commitDraft(...args),
  listIncomingDraftRows: (...args: unknown[]) => calls.listDrafts(...args),
  getIncoming: (...args: unknown[]) => calls.get(...args),
  addIncomingItem: vi.fn(),
  acceptIncoming: vi.fn(),
  rejectIncoming: vi.fn(),
  deleteIncomingItem: vi.fn(),
  createIncoming: vi.fn(),
  updateIncoming: vi.fn(),
  listIncoming: vi.fn(),
}));
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      id: "receiver-1",
      active_tenant_id: "tenant-1",
      is_developer: false,
      permissions: ["incoming.view", "incoming.edit"],
      permission_scopes: { "incoming.view": null, "incoming.edit": null },
    },
  }),
}));
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
  useBlocker: () => ({ status: "idle" }),
}));
vi.mock("@/features/catalog/CatalogPicker", () => ({
  CatalogPicker: ({
    id,
    onChange,
    initialLabel,
  }: {
    id?: string;
    onChange: (id: string, name: string) => void;
    initialLabel?: string;
  }) => (
    <div>
      <button id={id} type="button" onClick={() => onChange("catalog-2", "Другой товар")}>
        {initialLabel ?? "Выбрать товар"}
      </button>
      <button type="button" onClick={() => onChange("catalog-1", "Исходный товар")}>
        Вернуться к исходному товару
      </button>
    </div>
  ),
}));

import { AddItemForm } from "@/features/incoming/AddItemForm";
import {
  incomingKeys,
  useIncomingDocQuery,
  useUpdateIncomingItem,
} from "@/features/incoming/queries";
import { type IncomingDocumentWithItems } from "@/features/incoming/types";

const PACKAGING = {
  version: 1 as const,
  unit: "tablet" as const,
  units_per_pack: 30,
  dispense_step: 5,
};
const ITEM = {
  id: "item-1",
  document_id: "doc-1",
  catalog_id: "catalog-1",
  batch_number: "BATCH-1",
  manufactured_at: "2026-01-01",
  expires_at: "2099-12-31",
  qty: "2.000",
  purchase_price: "4.50",
  sale_price: "5.25",
  currency: "TJS",
  created_batch_id: null,
  catalog_name: "Исходный товар",
  unit_packaging_preparation: null,
};
type Item = NonNullable<React.ComponentProps<typeof AddItemForm>["item"]>;

function setup(item?: Item) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <AddItemForm documentId="doc-1" item={item} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

function checkbox() {
  return screen.getByRole("checkbox", { name: "Сохранить состав упаковки" });
}

function save() {
  fireEvent.click(screen.getByRole("button", { name: "Сохранить", exact: true }));
}

async function sentPayload(): Promise<Record<string, unknown>> {
  await waitFor(() => expect(calls.update).toHaveBeenCalledTimes(1));
  const args = calls.update.mock.calls[0] as unknown[];
  expect(args.slice(0, 2)).toEqual(["doc-1", "item-1"]);
  return args[2] as Record<string, unknown>;
}

describe("Incoming saved-item packaging preparation", () => {
  beforeEach(() => {
    calls.get.mockReset();
    calls.update.mockReset();
    calls.update.mockResolvedValue(ITEM);
    calls.saveDraft.mockReset();
    calls.saveDraft.mockResolvedValue({ revision: 1 });
    calls.commitDraft.mockReset();
    calls.commitDraft.mockResolvedValue(ITEM);
    calls.listDrafts.mockReset();
    calls.listDrafts.mockResolvedValue([]);
  });

  it("explicitly prepares a saved row while keeping quantity and both prices in packs", async () => {
    const { onClose } = setup(ITEM);
    expect(checkbox()).not.toBeChecked();
    fireEvent.click(checkbox());
    save();
    expect(await sentPayload()).toMatchObject({
      prepare_unit_sale: true,
      qty: "2.000",
      purchase_price: "4.50",
      sale_price: "5.25",
    });
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("loads historical packaging and explicitly clears it when unchecked", async () => {
    setup({ ...ITEM, unit_packaging_preparation: PACKAGING });
    expect(checkbox()).toBeChecked();
    expect(screen.getByText(/30/)).toHaveTextContent(/5/);
    fireEvent.click(checkbox());
    save();
    expect(await sentPayload()).toMatchObject({
      prepare_unit_sale: false,
      purchase_price: "4.50",
      sale_price: "5.25",
    });
  });

  it.each([false, true])(
    "omits the flag during ordinary edits with historical preparation=%s",
    async (prepared) => {
      setup({ ...ITEM, unit_packaging_preparation: prepared ? PACKAGING : null });
      fireEvent.change(screen.getByLabelText("Номер партии"), { target: { value: "BATCH-2" } });
      save();
      const payload = await sentPayload();
      expect(payload).toMatchObject({
        batch_number: "BATCH-2",
        purchase_price: "4.50",
        sale_price: "5.25",
      });
      expect(payload).not.toHaveProperty("prepare_unit_sale");
      expect(payload).not.toHaveProperty("unit_packaging_preparation");
    },
  );

  it("does not re-prepare the frozen snapshot after toggling back to its original state", async () => {
    setup({ ...ITEM, unit_packaging_preparation: PACKAGING });
    fireEvent.click(checkbox());
    fireEvent.click(checkbox());
    save();
    expect(await sentPayload()).not.toHaveProperty("prepare_unit_sale");
  });

  it.each(["2.5", "2,5"])(
    "blocks fractional packs %s when preparation is selected",
    async (quantity) => {
      const { onClose } = setup(ITEM);
      fireEvent.click(checkbox());
      fireEvent.change(screen.getByLabelText(/Количество/), { target: { value: quantity } });
      save();
      expect(await screen.findByText(/цел.*упаков|упаков.*цел/i)).toBeInTheDocument();
      expect(calls.update).not.toHaveBeenCalled();
      expect(onClose).not.toHaveBeenCalled();
    },
  );

  it("keeps legacy fractional quantities valid when preparation is not selected", async () => {
    setup(ITEM);
    fireEvent.change(screen.getByLabelText(/Количество/), { target: { value: "2,5" } });
    save();
    const payload = await sentPayload();
    expect(payload.qty).toBe("2.5");
    expect(payload).not.toHaveProperty("prepare_unit_sale");
  });

  it("clears preparation and historical packaging when choosing a different catalog item", async () => {
    setup({ ...ITEM, unit_packaging_preparation: PACKAGING });
    expect(checkbox()).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Лекарство или товар" }));
    expect(checkbox()).not.toBeChecked();
    expect(screen.queryByText(/30/)).not.toBeInTheDocument();
    save();
    expect(await sentPayload()).toMatchObject({
      catalog_id: "catalog-2",
      prepare_unit_sale: false,
    });
  });

  it("allows explicit preparation for the new catalog without copying historical packaging", async () => {
    setup({ ...ITEM, unit_packaging_preparation: PACKAGING });
    fireEvent.click(screen.getByRole("button", { name: "Лекарство или товар" }));
    fireEvent.click(checkbox());
    expect(screen.queryByText(/30/)).not.toBeInTheDocument();
    save();
    expect(await sentPayload()).toMatchObject({
      catalog_id: "catalog-2",
      prepare_unit_sale: true,
      purchase_price: "4.50",
      sale_price: "5.25",
    });
  });

  it("preserves the explicit choice and prices after a server error for retry", async () => {
    calls.update.mockRejectedValueOnce(new Error("request failed"));
    const { onClose } = setup(ITEM);
    fireEvent.click(checkbox());
    save();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(checkbox()).toBeChecked();
    expect(screen.getByLabelText(/Закупочная цена/)).toHaveValue("4.50");
    expect(screen.getByLabelText(/Розничная цена/)).toHaveValue("5.25");
    expect(onClose).not.toHaveBeenCalled();
    save();
    await waitFor(() => expect(calls.update).toHaveBeenCalledTimes(2));
    expect(calls.update.mock.calls[1]?.[2]).toMatchObject({ prepare_unit_sale: true });
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("does not expose the setting on new draft rows", () => {
    setup();
    expect(
      screen.queryByRole("checkbox", { name: "Сохранить состав упаковки" }),
    ).not.toBeInTheDocument();
    expect(calls.update).not.toHaveBeenCalled();
    expect(calls.saveDraft).not.toHaveBeenCalled();
    expect(calls.commitDraft).not.toHaveBeenCalled();
  });

  it("publishes the saved item before resolving the mutation and ignores a late document read", async () => {
    const otherItem = { ...ITEM, id: "item-2" };
    const document: IncomingDocumentWithItems = {
      id: "doc-1",
      tenant_id: "tenant-1",
      branch_id: "branch-1",
      supplier_id: "supplier-1",
      document_number: null,
      document_date: "2026-10-29",
      status: "draft",
      total_amount: null,
      currency: "TJS",
      notes: null,
      document_file_path: null,
      created_at: "2026-10-29T00:00:00Z",
      updated_at: "2026-10-29T00:00:00Z",
      accepted_at: null,
      items: [{ ...ITEM, unit_packaging_preparation: PACKAGING }, otherItem],
    };
    const savedItem = { ...ITEM, unit_packaging_preparation: PACKAGING, sale_price: "6.25" };
    const patch = deferred<Item>();
    const staleRead = deferred<IncomingDocumentWithItems>();
    const freshRead = deferred<IncomingDocumentWithItems>();
    calls.update.mockReturnValueOnce(patch.promise);
    calls.get.mockReturnValueOnce(staleRead.promise).mockReturnValueOnce(freshRead.promise);
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: Infinity, gcTime: 0 },
        mutations: { retry: false },
      },
    });
    const key = incomingKeys.doc(document.id);
    client.setQueryData(key, document);
    const { result, unmount } = renderHook(
      () => ({ document: useIncomingDocQuery(document.id), update: useUpdateIncomingItem() }),
      {
        wrapper: ({ children }: { children: ReactNode }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    try {
      let mutation!: Promise<Item>;
      act(() => {
        mutation = result.current.update.mutateAsync({
          documentId: document.id,
          itemId: ITEM.id,
          payload: { sale_price: savedItem.sale_price },
        });
      });
      await waitFor(() => expect(calls.update).toHaveBeenCalledOnce());
      let staleFetch!: Promise<void>;
      act(() => {
        staleFetch = client.refetchQueries({ queryKey: key });
      });
      expect(calls.get).toHaveBeenCalledOnce();
      const staleSignal = calls.get.mock.calls[0]?.[1] as AbortSignal;
      expect(staleSignal.aborted).toBe(false);

      await act(async () => {
        patch.resolve(savedItem);
        await mutation;
      });
      expect(client.getQueryData(key)).toEqual({ ...document, items: [savedItem, otherItem] });
      expect(staleSignal.aborted).toBe(true);
      expect(calls.get).toHaveBeenCalledTimes(2);
      await waitFor(() => {
        expect(result.current.document.data?.items[0]).toEqual(savedItem);
        expect(result.current.update.isPending).toBe(false);
        expect(result.current.document.isFetching).toBe(true);
      });

      // Even a transport that ignores cancellation must not restore the pre-PATCH item.
      await act(async () => {
        staleRead.resolve(document);
        await staleRead.promise;
      });
      expect(client.getQueryData<IncomingDocumentWithItems>(key)?.items[0]).toEqual(savedItem);

      const refreshed = { ...document, items: [savedItem, otherItem], notes: "Обновлено" };
      await act(async () => {
        freshRead.resolve(refreshed);
        await freshRead.promise;
        await staleFetch;
      });
      await waitFor(() => expect(result.current.document.isFetching).toBe(false));
      expect(result.current.document.data).toEqual(refreshed);
    } finally {
      unmount();
      client.clear();
    }
  });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}
