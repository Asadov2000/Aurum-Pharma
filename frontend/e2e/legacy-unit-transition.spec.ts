import { request, test } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  seedAcceptedBatch,
  seedBranch,
  seedCatalogItem,
  seedSupplier,
  uniqueName,
} from "./helpers";

for (const scenario of [
  { name: "closed", closed: 3, opened: false, quantity: "3.000", units: "60" },
  { name: "mixed opened", closed: 1, opened: true, quantity: "1.750", units: "35" },
  { name: "opened only", closed: 0, opened: true, quantity: "0.750", units: "15" },
]) {
  test(`${scenario.name} transfer recovers a committed result without a second stock command`, async ({
    page,
  }) => {
    test.setTimeout(120_000);
    clearLoginRateLimit(OWNER.email);
    const anonymous = await request.newContext();
    const tokens = await apiLogin(anonymous, OWNER);
    const api = await apiContext(tokens.access_token);
    try {
      const branch = await seedBranch(api, uniqueName("Legacy transfer branch"));
      const supplier = await seedSupplier(api, uniqueName("Legacy transfer supplier"));
      const product = await seedCatalogItem(api, uniqueName("Legacy transfer medicine"), "10.00");
      const expiry = new Date();
      expiry.setUTCDate(expiry.getUTCDate() + 180);
      const batchNumber = uniqueName("Transfer");
      const batch = await seedAcceptedBatch(api, {
        branchId: branch.id,
        supplierId: supplier.id,
        catalogId: product.id,
        qty: scenario.quantity,
        purchasePrice: "5.00",
        salePrice: "10.00",
        expiresAt: expiry.toISOString().slice(0, 10),
        batchNumber,
      });
      await page.setViewportSize({ width: 1100, height: 850 });
      await loginInBrowser(page, OWNER);
      await page.goto("/batches");
      await page.getByLabel("Номер партии", { exact: true }).fill(batchNumber);
      await page
        .getByRole("button", {
          name: `Открыть партию ${batchNumber} товара ${product.brand_name}`,
          exact: true,
        })
        .click();
      const panel = page.getByRole("region", { name: "Перенос в поштучный учёт", exact: true });
      await panel.getByLabel("Единица", { exact: true }).selectOption("tablet");
      await panel.getByLabel("Единиц в упаковке", { exact: true }).fill("20");
      await panel.getByLabel("Единиц в блистере (необязательно)", { exact: true }).fill("10");
      await panel.getByLabel("Шаг отпуска", { exact: true }).fill("1");
      await panel.getByLabel("Закрытых упаковок", { exact: true }).fill(String(scenario.closed));
      if (scenario.opened) {
        await panel
          .getByRole("button", { name: "Добавить открытую упаковку", exact: true })
          .click();
        await panel.getByLabel("Дата и время вскрытия 1", { exact: true }).fill("2026-10-01T10:00");
        await panel.getByLabel("Целые блистеры 1", { exact: true }).fill("1");
        await panel.getByRole("button", { name: "Добавить вскрытый блистер", exact: true }).click();
        await panel.getByLabel("Номер блистера 1.1", { exact: true }).fill("2");
        await panel.getByLabel("Остаток блистера 1.1", { exact: true }).fill("5");
      } else {
        await panel.getByLabel("Подтверждаю: открытых упаковок нет", { exact: true }).check();
      }
      const previewRequest = page.waitForRequest((req) =>
        req.url().endsWith(`/batches/${batch.id}/unit-reconciliation/preview`),
      );
      await panel.getByRole("button", { name: "Проверить пересчёт", exact: true }).click();
      await expect(
        panel.getByText("Количество совпадает. Это ещё не перенос остатков.", { exact: true }),
      ).toBeVisible();
      const observed = (await previewRequest).postDataJSON() as {
        opened_packs: {
          id: string;
          opened_at: string;
          intact_blisters: number[];
          opened_blisters: { number: number; remaining_units: number }[];
          loose_units: number;
        }[];
      };
      const saveRequest = page.waitForRequest((req) =>
        req.url().endsWith(`/batches/${batch.id}/unit-reconciliation/records`),
      );
      await panel.getByRole("button", { name: "Сохранить пересчёт", exact: true }).click();
      const saved = (await saveRequest).postDataJSON() as {
        reconciliation_id: string;
        opened_packs: typeof observed.opened_packs;
      };
      expect(saved.opened_packs).toEqual(observed.opened_packs);
      if (scenario.opened) {
        expect(saved.opened_packs).toEqual([
          {
            id: expect.stringMatching(/^[0-9a-f-]{36}$/),
            opened_at: "2026-10-01T10:00:00+05:00",
            intact_blisters: [1],
            opened_blisters: [{ number: 2, remaining_units: 5 }],
            loose_units: 0,
          },
        ]);
      }
      await expect(panel.getByText(/Пересчёт сохранён:/)).toBeVisible();
      const before = await api.get(`batches/${batch.id}`);
      expect(before.ok()).toBe(true);
      expect(((await before.json()) as { qty_remaining: string }).qty_remaining).toBe(
        scenario.quantity,
      );

      const commands: string[] = [];
      let completed: { command_id: string; lot_id: string } | undefined;
      await page.route(`**/api/v1/batches/${batch.id}/unit-transition`, async (route) => {
        commands.push((route.request().postDataJSON() as { command_id: string }).command_id);
        const response = await route.fetch();
        expect(response.ok()).toBe(true);
        completed = (await response.json()) as { command_id: string; lot_id: string };
        await route.abort("failed");
      });
      await panel.getByRole("button", { name: "Перенести остаток", exact: true }).click();
      expect(commands).toHaveLength(0);
      await page.getByRole("button", { name: "Подтвердить перенос", exact: true }).click();
      await expect(panel.getByRole("alert")).toBeVisible();
      expect(commands).toHaveLength(1);
      await panel.getByRole("button", { name: "Проверить перенос", exact: true }).click();
      await expect(panel.getByText(/Перенос подтверждён\. Физическая партия:/)).toBeVisible();
      expect(completed?.command_id).toBe(commands[0]);
      expect(commands).toHaveLength(1);
      const after = await api.get(`batches/${batch.id}`);
      expect(after.ok()).toBe(true);
      expect(((await after.json()) as { qty_remaining: string }).qty_remaining).toBe("0.000");
      const recoveredRecord = await api.get(
        `batches/${batch.id}/unit-reconciliation/records/${saved.reconciliation_id}`,
        { params: { branch_id: branch.id } },
      );
      expect(recoveredRecord.ok()).toBe(true);
      const recovered = (await recoveredRecord.json()) as {
        preview: { observation: { opened_packs: typeof observed.opened_packs } };
      };
      const normalize = (packs: typeof observed.opened_packs) =>
        packs.map((pack) => ({ ...pack, opened_at: new Date(pack.opened_at).toISOString() }));
      expect(normalize(recovered.preview.observation.opened_packs)).toEqual(
        normalize(saved.opened_packs),
      );
      const lots = await api.get("batches/physical", {
        params: { branch_id: branch.id, show_empty: true },
      });
      expect(lots.ok()).toBe(true);
      const physical = (await lots.json()) as {
        items: { lot_id: string; base_units: string; closed_packs: string; opened_units: string }[];
      };
      expect(physical.items).toHaveLength(1);
      expect(physical.items[0]).toMatchObject({
        lot_id: completed?.lot_id,
        closed_packs: String(scenario.closed),
        base_units: scenario.units,
        opened_units: scenario.opened ? "15" : "0",
      });
    } finally {
      await api.dispose();
      await anonymous.dispose();
    }
  });
}
