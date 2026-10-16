import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PhysicalWriteOffModal } from "@/features/inventory/PhysicalWriteOffModal";
import type { PhysicalWriteoffStock } from "@/features/inventory/physicalWriteoff";

const mocks = vi.hoisted(() => ({ stock: vi.fn() }));
vi.mock("@/features/auth/hooks", () => ({ useAuth: () => ({ user: { id: "actor" } }) }));
vi.mock("@/features/inventory/physicalWriteoff", () => ({
  getPhysicalWriteoffStock: (...args: unknown[]) => mocks.stock(...args),
}));

const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
function stock(): PhysicalWriteoffStock {
  return {
    lot: {
      lot_id: id(1),
      branch_id: id(2),
      catalog_id: id(3),
      catalog_name: "Тестовый препарат",
      expires_at: "2027-01-01",
      closed_packs: "2",
      opened_units: "15",
      base_units: "55",
      is_blocked: false,
      blocking_revision: "0",
      packaging: {
        version: 2,
        unit: "tablet",
        units_per_pack: 20,
        units_per_blister: 10,
        dispense_step: 2,
      },
    },
    opened_packs: [
      {
        pack_id: id(4),
        remaining_units: "15",
        state: {
          intact_blisters: [1],
          opened_blisters: [{ number: 2, remaining_units: 5 }],
          loose_units: 0,
        },
      },
    ],
    total: 1,
    page: 1,
    page_size: 50,
  };
}
let client: QueryClient;
const submit = vi.fn();
function view(writable = true) {
  return (
    <QueryClientProvider client={client}>
      <PhysicalWriteOffModal
        lot={stock().lot}
        writable={writable}
        onClose={vi.fn()}
        onSubmit={submit}
      />
    </QueryClientProvider>
  );
}
async function ready() {
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Проверить списание" })).toBeEnabled(),
  );
}
function change(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  mocks.stock.mockResolvedValue(stock());
  submit.mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  client.clear();
});

describe("physical write-off explicit selection", () => {
  it("retains selections from separate pages under their original pack identities", async () => {
    mocks.stock.mockImplementation(async (_lot: string, _branch: string, page: number) => {
      const data = stock();
      data.total = 51;
      data.page = page;
      if (page === 2) data.opened_packs[0].pack_id = id(8);
      return data;
    });
    render(view());
    await ready();
    change(`Блистер 2 · ${id(4)}`, "2");
    fireEvent.click(screen.getByRole("button", { name: "Вперёд" }));
    await screen.findByLabelText(`Блистер 2 · ${id(8)}`);
    await ready();
    change(`Блистер 2 · ${id(8)}`, "1");
    fireEvent.click(screen.getByRole("button", { name: "Проверить списание" }));
    expect(await screen.findByText("К списанию: 3 базовых единиц.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить списание" }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        selection: {
          closed_packs: 0,
          opened_packs: [
            { pack_id: id(4), blisters: [{ number: 2, units: 2 }], loose_units: 0 },
            { pack_id: id(8), blisters: [{ number: 2, units: 1 }], loose_units: 0 },
          ],
        },
      }),
      "3",
    );
  });

  it("reviews closed packs and one exact damaged unit without applying the sale step", async () => {
    render(view());
    await ready();
    expect(submit).not.toHaveBeenCalled();
    change("Закрытые упаковки", "1");
    change(`Блистер 2 · ${id(4)}`, "1");
    change("Комментарий", "Повреждение оболочки");
    fireEvent.click(screen.getByRole("button", { name: "Проверить списание" }));
    expect(await screen.findByText("К списанию: 21 базовых единиц.")).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить списание" }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit).toHaveBeenCalledWith(
      {
        branch_id: id(2),
        lot_id: id(1),
        reason: "damaged",
        comment: "Повреждение оболочки",
        selection: {
          closed_packs: 1,
          opened_packs: [{ pack_id: id(4), blisters: [{ number: 2, units: 1 }], loose_units: 0 }],
        },
      },
      "21",
    );
  });

  it.each(["-1", "1.5", "1e2", "999999999999"])("rejects malformed quantity %s", async (value) => {
    render(view());
    await ready();
    change("Закрытые упаковки", value);
    fireEvent.click(screen.getByRole("button", { name: "Проверить списание" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("целые неотрицательные");
    expect(screen.queryByRole("button", { name: "Подтвердить списание" })).not.toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
  });

  it("rejects empty and overdrawn exact components instead of opening replacement cartons", async () => {
    render(view());
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Проверить списание" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Выберите количество");
    change("Закрытые упаковки", "3");
    fireEvent.click(screen.getByRole("button", { name: "Проверить списание" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Закрытых упаковок недостаточно"),
    );
    change("Закрытые упаковки", "0");
    change(`Блистер 2 · ${id(4)}`, "6");
    fireEvent.click(screen.getByRole("button", { name: "Проверить списание" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("конкретном блистере"));
    expect(submit).not.toHaveBeenCalled();
  });

  it("keeps loose selection attached to its original pack", async () => {
    const data = stock();
    data.lot.packaging.units_per_blister = null;
    data.opened_packs[0].state = { intact_blisters: [], opened_blisters: [], loose_units: 15 };
    mocks.stock.mockResolvedValue(data);
    render(view());
    await ready();
    change(`Россыпь · ${id(4)}`, "2");
    fireEvent.click(screen.getByRole("button", { name: "Проверить списание" }));
    fireEvent.click(await screen.findByRole("button", { name: "Подтвердить списание" }));
    await waitFor(() =>
      expect(submit).toHaveBeenCalledWith(
        expect.objectContaining({
          selection: {
            closed_packs: 0,
            opened_packs: [{ pack_id: id(4), blisters: [], loose_units: 2 }],
          },
        }),
        "2",
      ),
    );
  });

  it("requires another review after editing and prevents a double confirmation", async () => {
    let finish!: () => void;
    submit.mockReturnValue(
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
    );
    render(view());
    await ready();
    change("Закрытые упаковки", "1");
    fireEvent.click(screen.getByRole("button", { name: "Проверить списание" }));
    fireEvent.click(await screen.findByRole("button", { name: "Изменить выбор" }));
    expect(screen.queryByRole("button", { name: "Подтвердить списание" })).not.toBeInTheDocument();
    change("Закрытые упаковки", "2");
    fireEvent.click(screen.getByRole("button", { name: "Проверить списание" }));
    const confirm = await screen.findByRole("button", { name: "Подтвердить списание" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(submit).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish();
    });
  });

  it("disables confirmation when access becomes readonly after review", async () => {
    const rendered = render(view());
    await ready();
    change("Закрытые упаковки", "1");
    fireEvent.click(screen.getByRole("button", { name: "Проверить списание" }));
    await screen.findByRole("button", { name: "Подтвердить списание" });
    rendered.rerender(view(false));
    expect(screen.getByRole("button", { name: "Подтвердить списание" })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
  });

  it("does not review blocked stock or a failed stock read", async () => {
    mocks.stock.mockResolvedValue({ ...stock(), lot: { ...stock().lot, is_blocked: true } });
    render(view());
    expect(await screen.findByRole("alert")).toHaveTextContent("Партия заблокирована");
    expect(screen.getByRole("button", { name: "Проверить списание" })).toBeDisabled();
    mocks.stock.mockRejectedValue(new Error("Состав недоступен"));
    fireEvent.click(screen.getByRole("button", { name: "Обновить остатки" }));
    expect(await screen.findByText("Не удалось загрузить состав остатка.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Проверить списание" })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
  });

  it("revalidates an already selected blister against refreshed remaining stock", async () => {
    render(view());
    await ready();
    change(`Блистер 2 · ${id(4)}`, "4");
    const fresh = stock();
    fresh.opened_packs[0].state.opened_blisters[0].remaining_units = 2;
    fresh.opened_packs[0].remaining_units = "12";
    fresh.lot.opened_units = "12";
    fresh.lot.base_units = "52";
    mocks.stock.mockResolvedValue(fresh);
    fireEvent.click(screen.getByRole("button", { name: "Обновить остатки" }));
    await waitFor(() => expect(mocks.stock).toHaveBeenCalledTimes(2));
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Проверить списание" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("конкретном блистере");
    expect(submit).not.toHaveBeenCalled();
  });
});
