import { test, type Page } from "@playwright/test";

import { clearLoginRateLimit, DEV, expect, loginInBrowser } from "./helpers";

const OVERVIEW = {
  generated_at: "2026-08-12T12:00:00Z",
  summary: {
    total_nodes: 1,
    healthy_nodes: 0,
    delayed_nodes: 1,
    offline_nodes: 0,
    critical_nodes: 0,
    revoked_nodes: 0,
    never_connected_nodes: 0,
    expiring_credentials: 0,
    pending_handovers: 0,
  },
  tenants: [{ tenant_id: "tenant-1", tenant_name: "Аптека Сино", node_count: 1 }],
  items: [
    {
      node_id: "node-1",
      tenant_id: "tenant-1",
      tenant_name: "Аптека Сино",
      branch_id: "branch-1",
      branch_name: "Филиал Рудаки",
      register_id: "register-1",
      register_name: "Касса 01",
      display_name: "Edge Рудаки 01",
      mode: "shadow_readonly",
      node_status: "active",
      health: "delayed",
      contact_state: "recent",
      integrity_state: "stale_report",
      credential_expires_at: "2026-10-01T00:00:00Z",
      last_seen_at: "2026-08-12T11:58:00Z",
      latest_report_at: "2026-08-12T11:45:00Z",
      latest_report_status: "matched",
      source_verified: true,
      writer_epoch: 1,
      current_sequence: 125,
      reported_sequence: 120,
      lag_events: 5,
    },
  ],
  total: 1,
  limit: 25,
  offset: 0,
};

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const width = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(width.document).toBeLessThanOrEqual(width.viewport + 1);
}

test("platform sync center stays read-only and usable on desktop and touch", async ({ page }) => {
  clearLoginRateLimit(DEV.email);
  await loginInBrowser(page, DEV);
  await page.route("**/api/v1/admin/sync/overview**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(OVERVIEW),
    });
  });

  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto("/admin");
  await page.getByRole("link", { name: /Синхронизация/ }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Синхронизация" })).toBeVisible();
  await expect(page.getByText("Edge Рудаки 01").first()).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expect(page.getByRole("button", { name: /отозвать|ротация|переключить/i })).toHaveCount(0);

  await page.getByRole("button", { name: "Подробнее об узле Edge Рудаки 01" }).click();
  await expect(page.getByRole("dialog", { name: "Узел: Edge Рудаки 01" })).toBeVisible();
  await page.getByRole("button", { name: "Закрыть", exact: true }).first().click();

  const filteredRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return (
      url.pathname.endsWith("/admin/sync/overview") && url.searchParams.get("health") === "delayed"
    );
  });
  await page.getByRole("combobox", { name: "Состояние", exact: true }).selectOption("delayed");
  await filteredRequest;

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: /Edge Рудаки 01/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
