import { request, test, type Locator, type Page } from "@playwright/test";

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

const WORKSPACES = [
  { path: "/suppliers", table: "Поставщики", open: /Открыть карточку/, kind: "supplier" },
  { path: "/incoming", table: "Приходы", open: /Открыть (приход|документ)/, kind: "incoming" },
  { path: "/batches", table: "Партии товаров", open: /Открыть партию/, kind: "batch" },
] as const;
type Workspace = (typeof WORKSPACES)[number];

// Existing split rows mark the selected record visually. No tabindex/role is
// added to the row: keyboard activation must remain on its native buttons.
const SELECTED_ROW = /shadow-\[inset_3px/;

test.beforeAll(async () => {
  // A standalone run starts without suppliers or incoming documents. Own three
  // complete records so both row selection and card scrolling are independent
  // of earlier specs and their incidental data.
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  try {
    const tokens = await apiLogin(anonymous, OWNER);
    const api = await apiContext(tokens.access_token);
    try {
      const branch = await seedBranch(api, uniqueName("Record selection branch"));
      const item = await seedCatalogItem(api, uniqueName("Record selection medicine"));
      const expiry = new Date();
      expiry.setUTCDate(expiry.getUTCDate() + 180);
      for (let index = 1; index <= 3; index += 1) {
        const supplier = await seedSupplier(api, uniqueName(`Record selection supplier ${index}`));
        await seedAcceptedBatch(api, {
          branchId: branch.id,
          supplierId: supplier.id,
          catalogId: item.id,
          qty: "3",
          purchasePrice: "4.00",
          salePrice: "5.00",
          expiresAt: expiry.toISOString().slice(0, 10),
          batchNumber: uniqueName(`Record selection batch ${index}`),
        });
      }
    } finally {
      await api.dispose();
    }
  } finally {
    await anonymous.dispose();
  }
});

async function selectCellText(page: Page, cell: Locator) {
  await cell.scrollIntoViewIfNeeded();
  const bounds = await cell.evaluate((element) => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node && !node.textContent?.trim()) node = walker.nextNode();
    if (!node) throw new Error("Seeded free cell has no text to select");
    const range = document.createRange();
    range.selectNodeContents(node);
    const rect = range.getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
  });
  expect(bounds.width).toBeGreaterThan(8);
  await page.mouse.move(bounds.x + 1, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + Math.min(bounds.width - 1, 120), bounds.y + bounds.height / 2, {
    steps: 12,
  });
  await page.mouse.up();
  expect(await page.evaluate(() => document.getSelection()?.toString().trim())).toBeTruthy();
}

async function expectOpened(
  page: Page,
  workspace: Workspace,
  title: string,
  batchNumber: string | null,
) {
  if (workspace.kind === "incoming") {
    await expect(page).toHaveURL(/\/incoming\/[0-9a-f-]{36}$/);
    const heading = title.startsWith("Приход № ")
      ? title.replace("Приход № ", "Приёмка № ")
      : "Приёмка без номера";
    await expect(page.getByRole("heading", { level: 1, name: heading, exact: true })).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  } else {
    await expect(page.getByRole("dialog")).toHaveCount(1);
    await expect(page.getByRole("dialog")).toContainText(title);
    if (batchNumber !== null) {
      await expect(page.getByRole("dialog")).toHaveAccessibleName(
        batchNumber === "Без номера" ? "Карточка партии" : `Партия ${batchNumber}`,
      );
    }
    expect(new URL(page.url()).pathname).toBe(workspace.path);
  }
}

async function cardBatchNumber(card: Locator, workspace: Workspace): Promise<string | null> {
  if (workspace.kind !== "batch") return null;
  return (
    await card.getByText("Партия", { exact: true }).locator("..").locator("p").nth(1).innerText()
  ).trim();
}

async function swipeCardUp(page: Page, card: Locator) {
  await card.scrollIntoViewIfNeeded();
  const bounds = await card.boundingBox();
  expect(bounds).not.toBeNull();
  if (!bounds) throw new Error("Seeded card must be visible before a touch gesture");
  const viewport = page.viewportSize();
  if (!viewport) throw new Error("Mobile viewport is required");
  const x = bounds.x + 16;
  const startY = Math.min(bounds.y + bounds.height - 24, viewport.height - 70);
  const endY = Math.max(100, startY - 230);
  expect(startY - endY).toBeGreaterThan(100);
  const before = await page.evaluate(() => window.scrollY);
  const client = await page.context().newCDPSession(page);
  try {
    await client.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x, y: startY }],
    });
    for (let step = 1; step <= 12; step += 1) {
      await client.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [{ x, y: startY + ((endY - startY) * step) / 12 }],
      });
      // A real gesture spans rendered frames; this is its cadence, not a load wait.
      await page.waitForTimeout(16);
    }
    // Stop the finger before lifting it: a fast release starts a fling whose
    // next tap only stops scrolling. This hold is part of the gesture cadence.
    await page.waitForTimeout(200);
    await client.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [{ x, y: endY }],
    });
    await client.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before + 40);
  } finally {
    await client.detach();
  }
}

test.describe("Whole-record selection on desktop", () => {
  test.use({ viewport: { width: 1536, height: 960 } });
  test.beforeEach(async ({ page }) => {
    clearLoginRateLimit(OWNER.email);
    await loginInBrowser(page, OWNER);
  });

  for (const workspace of WORKSPACES) {
    test(`${workspace.kind}: free cells, text selection and native keyboard controls`, async ({
      page,
    }) => {
      await page.goto(workspace.path);
      const table = page.getByRole("table", { name: workspace.table, exact: true });
      const rows = table.locator("tbody tr");
      await expect(rows.nth(1)).toBeVisible();
      const first = rows.nth(0);
      const second = rows.nth(1);
      const freeCell = second.locator("td").nth(1);
      await expect(first).toHaveClass(SELECTED_ROW);
      await expect(second).not.toHaveClass(SELECTED_ROW);

      await selectCellText(page, freeCell);
      await expect(first).toHaveClass(SELECTED_ROW);
      await expect(second).not.toHaveClass(SELECTED_ROW);
      await page.evaluate(() => document.getSelection()?.removeAllRanges());

      await freeCell.click();
      await expect(second).toHaveClass(SELECTED_ROW);
      await expect(first).not.toHaveClass(SELECTED_ROW);
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect(new URL(page.url()).pathname).toBe(workspace.path);

      const firstButton = first.getByRole("button", { name: workspace.open });
      const secondButton = second.getByRole("button", { name: workspace.open });
      await firstButton.click();
      await expect(first).toHaveClass(SELECTED_ROW);
      await expect(second).not.toHaveClass(SELECTED_ROW);
      await secondButton.focus();
      await page.keyboard.press("Enter");
      await expect(second).toHaveClass(SELECTED_ROW);
      await firstButton.focus();
      await page.keyboard.press("Space");
      await expect(first).toHaveClass(SELECTED_ROW);
      await expect(second).not.toHaveClass(SELECTED_ROW);
      await expect(table.locator("tbody tr[class*='shadow-[inset_3px']")).toHaveCount(1);
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect(new URL(page.url()).pathname).toBe(workspace.path);
    });
  }
});

test.describe("Whole-record touch interaction in Chromium mobile emulation", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  test.beforeEach(async ({ page }) => {
    clearLoginRateLimit(OWNER.email);
    await loginInBrowser(page, OWNER);
  });

  for (const workspace of WORKSPACES) {
    test(`${workspace.kind}: swiping preserves the list; free-card and native taps open one record`, async ({
      page,
    }) => {
      await page.goto(workspace.path);
      const cards = page.locator("main article").filter({
        has: page.getByRole("button", { name: workspace.open }),
      });
      await expect(cards.nth(1)).toBeVisible();
      const first = cards.first();
      await swipeCardUp(page, first);
      expect(new URL(page.url()).pathname).toBe(workspace.path);
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(cards.nth(1)).toBeVisible();

      const heading = first.getByRole("heading", { level: 2 });
      const title = (await heading.innerText()).trim();
      const batchNumber = await cardBatchNumber(first, workspace);
      await heading.tap();
      await expectOpened(page, workspace, title, batchNumber);

      // Reopen through the retained native action as a separate real interaction.
      await page.goto(workspace.path);
      const next = cards.nth(1);
      const nextTitle = (await next.getByRole("heading", { level: 2 }).innerText()).trim();
      const nextBatchNumber = await cardBatchNumber(next, workspace);
      await next.getByRole("button", { name: workspace.open }).tap();
      await expectOpened(page, workspace, nextTitle, nextBatchNumber);
    });
  }
});
