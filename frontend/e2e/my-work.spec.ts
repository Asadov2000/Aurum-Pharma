import { randomUUID } from "node:crypto";

import { expect, request, test, type Page, type Response } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  installBrowserSession,
  OWNER,
  seedBranch,
  seedRegister,
  uniqueName,
} from "./helpers";

// Authentication cookies and account contacts must not enter browser artifacts.
test.use({ trace: "off", screenshot: "off", video: "off" });

interface ShiftIdentity {
  id: string;
  opened_by_user_id: string;
}

interface PersonalShift {
  id: string;
  branch_id: string;
  branch_name: string;
  register_id: string;
  register_name: string;
  opened_at: string;
  closed_at: string | null;
  status: "open" | "closed" | "suspended";
  sales_total: string;
  returns_total: string;
  currency: string;
}

interface PersonalShiftList {
  items: PersonalShift[];
  total: number;
  page: number;
  page_size: number;
  timezone: string;
}

function waitForPersonalShifts(page: Page): Promise<Response> {
  return page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === "/api/v1/shifts/mine",
  );
}

async function expectOwnShiftPage(
  page: Page,
  response: Response,
  ownShift: PersonalShift,
  foreignShiftId: string,
  foreignRegisterName: string,
  timezone: string,
) {
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toContain("no-store");
  const shifts = (await response.json()) as PersonalShiftList;
  expect(shifts.timezone).toBe(timezone);
  expect(shifts.total).toBe(1);
  expect(shifts.items.map((shift) => shift.id)).toEqual([ownShift.id]);
  expect(shifts.items.some((shift) => shift.id === foreignShiftId)).toBe(false);

  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "Моя работа", exact: true })).toBeVisible();
  await expect(
    main.getByRole("heading", { name: "Мои кассовые смены", exact: true }),
  ).toBeVisible();
  const shiftList = main.getByRole("list", { name: "Ваши смены", exact: true });
  await expect(shiftList.getByRole("listitem")).toHaveCount(1);
  await expect(shiftList.getByRole("heading", { name: ownShift.branch_name })).toBeVisible();
  await expect(shiftList.getByText(ownShift.register_name, { exact: true })).toBeVisible();
  await expect(shiftList.getByText("Закрыта", { exact: true })).toBeVisible();
  await expect(shiftList.getByText("Продажи", { exact: true })).toBeVisible();
  await expect(shiftList.getByText("Возвраты", { exact: true })).toBeVisible();
  await expect(shiftList.getByText("0,00 TJS", { exact: true })).toHaveCount(2);
  await expect(shiftList.getByText(foreignRegisterName, { exact: true })).toHaveCount(0);
  const openedAt = new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: timezone,
  }).format(new Date(ownShift.opened_at));
  await expect(shiftList.getByText(openedAt, { exact: true }).first()).toBeVisible();
  await expect(main.getByText(/не табель рабочего времени/i)).toBeVisible();
  await expect(main.getByRole("link", { name: "Мой профиль", exact: true })).toHaveAttribute(
    "href",
    "/profile",
  );
  await expect(main.getByText("Нужна проверка сведений?", { exact: true })).toBeVisible();
  await main.getByText("Как читать выплаты", { exact: true }).click();
  await expect(main.getByText(/Превышение суммы не создаёт долг сотрудника/)).toBeVisible();
  await expect(main.getByRole("button", { name: "Показать сведения" })).toBeVisible();
  await expect(main.getByRole("list", { name: "Периоды зарплаты" })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
}

test("employee without reports permission sees only their own register shifts after direct mobile navigation and reload", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const ownerLoginApi = await request.newContext();
  const employeeLoginApi = await request.newContext();
  let ownerApi: Awaited<ReturnType<typeof apiContext>> | null = null;
  let employeeApi: Awaited<ReturnType<typeof apiContext>> | null = null;

  try {
    ownerApi = await apiContext((await apiLogin(ownerLoginApi, OWNER)).access_token);
    const settingsResponse = await ownerApi.get("tenant/settings");
    expect(settingsResponse.status()).toBe(200);
    const settings = (await settingsResponse.json()) as { report_timezone: string };
    const branch = await seedBranch(ownerApi, uniqueName("Моя работа — точка"));
    const ownRegister = await seedRegister(ownerApi, branch.id, uniqueName("Своя касса"));
    const foreignRegister = await seedRegister(ownerApi, branch.id, uniqueName("Чужая касса"));
    const permissions = ["pos.shift_open", "pos.shift_close", "pos.sell"];
    const roleResponse = await ownerApi.post("roles", {
      data: { name: uniqueName("Кассовые смены без отчётов"), permissions },
    });
    expect(roleResponse.status()).toBe(201);
    const role = (await roleResponse.json()) as { id: string };

    // A new employee keeps this fixture independent of shared cashier assignments.
    const email = `my-work-${randomUUID()}@e2e.aurum.tj`;
    const invitation = await ownerApi.post("users/invite", {
      data: {
        operation_id: randomUUID(),
        email,
        full_name: "Тест собственных смен",
        role_id: role.id,
        branch_id: branch.id,
      },
    });
    expect(invitation.status()).toBe(201);
    const employeeTokens = await apiLogin(employeeLoginApi, { email, password: "" });
    employeeApi = await apiContext(employeeTokens.access_token);
    const meResponse = await employeeApi.get("auth/me");
    expect(meResponse.status()).toBe(200);
    const employee = (await meResponse.json()) as { id: string; permissions: string[] };
    expect(employee.permissions).toEqual(expect.arrayContaining(permissions));
    expect(employee.permissions.includes("reports.view")).toBe(false);

    const opened = await employeeApi.post("shifts/open", {
      data: { register_id: ownRegister.id, opening_cash: "0.00" },
    });
    expect(opened.status()).toBe(201);
    const ownShift = (await opened.json()) as ShiftIdentity;
    expect(ownShift.opened_by_user_id).toBe(employee.id);
    const openMine = await employeeApi.get("shifts/mine");
    expect(openMine.status()).toBe(200);
    const openHistory = (await openMine.json()) as PersonalShiftList;
    expect(openHistory.items).toHaveLength(1);
    expect(openHistory.items[0]).toMatchObject({
      id: ownShift.id,
      status: "open",
      closed_at: null,
    });
    const closed = await employeeApi.post(`shifts/${ownShift.id}/close`, {
      data: { closing_cash_actual: "0.00" },
    });
    expect(closed.status()).toBe(200);

    // The other actor uses the same assigned branch, so branch scope cannot hide a leak.
    const foreignOpened = await ownerApi.post("shifts/open", {
      data: { register_id: foreignRegister.id, opening_cash: "0.00" },
    });
    expect(foreignOpened.status()).toBe(201);
    const foreignShift = (await foreignOpened.json()) as ShiftIdentity;
    expect(foreignShift.opened_by_user_id === employee.id).toBe(false);
    const foreignClosed = await ownerApi.post(`shifts/${foreignShift.id}/close`, {
      data: { closing_cash_actual: "0.00" },
    });
    expect(foreignClosed.status()).toBe(200);

    const mineResponse = await employeeApi.get("shifts/mine", {
      params: { page: 1, page_size: 10, cashier_id: foreignShift.opened_by_user_id },
    });
    expect(mineResponse.status()).toBe(200);
    const mine = (await mineResponse.json()) as PersonalShiftList;
    expect(mine).toMatchObject({
      total: 1,
      page: 1,
      page_size: 10,
      timezone: settings.report_timezone,
    });
    expect(mine.items).toHaveLength(1);
    const personalShift = mine.items[0]!;
    expect(personalShift).toMatchObject({
      id: ownShift.id,
      branch_id: branch.id,
      register_id: ownRegister.id,
      status: "closed",
      currency: "TJS",
    });
    expect(Number(personalShift.sales_total)).toBe(0);
    expect(Number(personalShift.returns_total)).toBe(0);
    expect(personalShift.closed_at).not.toBeNull();
    expect(mine.items.some((shift) => shift.id === foreignShift.id)).toBe(false);
    expect((await employeeApi.get("shifts")).status()).toBe(403);
    const reportDay = personalShift.opened_at.slice(0, 10);
    expect(
      (
        await employeeApi.get("reports/sales-summary", {
          params: { from: reportDay, to: reportDay },
        })
      ).status(),
    ).toBe(403);

    // Dismiss optional first-login suggestions only for this new test account.
    expect((await employeeApi.post("auth/password/prompt/dismiss", { data: {} })).ok()).toBe(true);
    expect((await employeeApi.post("auth/mfa/settings/dismiss")).ok()).toBe(true);
    await installBrowserSession(page, employeeTokens);
    await page.setViewportSize({ width: 390, height: 844 });
    const directResponse = waitForPersonalShifts(page);
    await page.goto("/my-work", { waitUntil: "domcontentloaded" });
    await expectOwnShiftPage(
      page,
      await directResponse,
      personalShift,
      foreignShift.id,
      foreignRegister.name,
      settings.report_timezone,
    );
    const reloadResponse = waitForPersonalShifts(page);
    await page.reload({ waitUntil: "domcontentloaded" });
    await expectOwnShiftPage(
      page,
      await reloadResponse,
      personalShift,
      foreignShift.id,
      foreignRegister.name,
      settings.report_timezone,
    );

    await page.setViewportSize({ width: 320, height: 720 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
    const main = page.getByRole("main");
    const shiftCard = main.getByRole("heading", { name: "Мои кассовые смены", exact: true });
    const salaryCard = main.getByRole("heading", { name: "Зарплата и выплаты", exact: true });
    const shiftBox = await shiftCard.boundingBox();
    const salaryBox = await salaryCard.boundingBox();
    expect(shiftBox).not.toBeNull();
    expect(salaryBox).not.toBeNull();
    expect(salaryBox!.x).toBeGreaterThan(shiftBox!.x + shiftBox!.width);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);

    await main.getByRole("link", { name: "Мой профиль", exact: true }).click();
    const profileWorkLink = page
      .getByRole("main")
      .getByRole("link", { name: "Моя работа", exact: true });
    await expect(profileWorkLink).toHaveAttribute("href", "/my-work");
    await profileWorkLink.click();
    await expect(page).toHaveURL(/\/my-work(?:\?|$)/);
    await expect(page.getByRole("main").getByRole("list", { name: "Ваши смены" })).toBeVisible();
  } finally {
    await employeeApi?.dispose();
    await ownerApi?.dispose();
    await employeeLoginApi.dispose();
    await ownerLoginApi.dispose();
  }
});
