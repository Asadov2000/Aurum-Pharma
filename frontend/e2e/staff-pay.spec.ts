import { randomUUID } from "node:crypto";

import { expect, request, test, type Locator, type Page, type Response } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  installBrowserSession,
  loginInBrowser,
  OWNER,
  seedBranch,
  uniqueName,
} from "./helpers";

// Salary details, contacts and authentication cookies must not enter browser artifacts.
test.use({ trace: "off", screenshot: "off", video: "off" });

interface StaffPayPeriod {
  id: string;
  employee_id: string;
  period_from: string;
  period_to: string;
  amount: string;
  paid_amount: string;
  advance_amount: string;
  remaining_amount: string;
  currency: "TJS";
  note: string;
  row_version: number;
  updated_at: string;
}

interface StaffPayEntry {
  id: string;
  kind: "payment" | "advance" | "reversal";
  amount: string;
  paid_on: string;
  note: string;
  is_reversed: boolean;
  reverses_entry_id: string | null;
}

interface StaffPayRevision {
  id: string;
  amount: string;
  note: string;
  row_version: number;
}

interface PageList<Item> {
  items: Item[];
  total: number;
  page: number;
  page_size: number;
}

interface StaffPayList extends PageList<StaffPayPeriod> {
  timezone: string;
}

type AuthenticatedApi = Awaited<ReturnType<typeof apiContext>>;

async function inviteEmployee(
  ownerApi: AuthenticatedApi,
  roleId: string,
  branchId: string,
  fullName: string,
) {
  const anonymous = await request.newContext();
  let api: AuthenticatedApi | null = null;
  try {
    const email = `staff-pay-${randomUUID()}@e2e.aurum.tj`;
    const invitation = await ownerApi.post("users/invite", {
      data: {
        operation_id: randomUUID(),
        email,
        full_name: fullName,
        role_id: roleId,
        branch_id: branchId,
      },
    });
    expect(invitation.status()).toBe(201);
    const tokens = await apiLogin(anonymous, { email, password: "" });
    api = await apiContext(tokens.access_token);
    const meResponse = await api.get("auth/me");
    expect(meResponse.status()).toBe(200);
    const identity = (await meResponse.json()) as { id: string; permissions: string[] };
    expect(identity.permissions.some((permission) => permission.startsWith("staff_pay."))).toBe(
      false,
    );
    expect((await api.post("auth/password/prompt/dismiss", { data: {} })).ok()).toBe(true);
    expect((await api.post("auth/mfa/settings/dismiss")).ok()).toBe(true);
    return { id: identity.id, fullName, tokens, api };
  } catch (error) {
    await api?.dispose();
    throw error;
  } finally {
    await anonymous.dispose();
  }
}

function waitForStaffPayResponse(page: Page, method: string, path: string): Promise<Response> {
  return page.waitForResponse(
    (response) =>
      response.request().method() === method &&
      new URL(response.url()).pathname === `/api/v1${path}`,
  );
}

async function expectPeriodTotals(
  ownerApi: AuthenticatedApi,
  employeeId: string,
  periodId: string,
  expected: { amount: number; paid: number; advance: number; remaining: number },
) {
  const response = await ownerApi.get("staff-pay", { params: { employee_id: employeeId } });
  expect(response.status()).toBe(200);
  const periods = (await response.json()) as StaffPayList;
  const period = periods.items.find((item) => item.id === periodId);
  if (!period) throw new Error("The selected employee's test period is unavailable");
  expect(Number(period.amount)).toBe(expected.amount);
  expect(Number(period.paid_amount)).toBe(expected.paid);
  expect(Number(period.advance_amount)).toBe(expected.advance);
  expect(Number(period.remaining_amount)).toBe(expected.remaining);
  expect(period.currency).toBe("TJS");
  return period;
}

async function expectPersonalStaffPay(
  page: Page,
  response: Response,
  ownPeriod: StaffPayPeriod,
  foreignPeriodId: string,
  timezone: string,
) {
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toContain("no-store");
  const periods = (await response.json()) as StaffPayList;
  expect(periods.timezone).toBe(timezone);
  expect(periods.total).toBe(1);
  expect(periods.items.map((period) => period.id)).toEqual([ownPeriod.id]);
  expect(periods.items.some((period) => period.id === foreignPeriodId)).toBe(false);
  expect(Number(periods.items[0]?.remaining_amount)).toBe(1000);
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "Моя работа", exact: true })).toBeVisible();
  await expect(
    main.getByRole("heading", { name: "Зарплата и выплаты", exact: true }),
  ).toBeVisible();
  await expect(main.locator('a[href="/staff-pay"]')).toHaveCount(0);
  await expect(
    main.getByRole("button", { name: /^(?:Добавить период|Изменить сумму|Записать выдачу)$/ }),
  ).toHaveCount(0);
  const periodsList = main.getByRole("list", { name: "Периоды зарплаты", exact: true });
  await expect(periodsList.getByRole("listitem")).toHaveCount(1);
  const card = periodsList.getByRole("listitem");
  await expectUiTotal(card, "Сумма за период", 1200);
  await expectUiTotal(card, "Выдано наличными", 200);
  await expectUiTotal(card, "Из них авансы", 200);
  await expectUiTotal(card, "Осталось выдать", 1000);
  await expect(card.getByText(ownPeriod.note, { exact: true })).toBeVisible();
  const updatedAt = new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: timezone,
  }).format(new Date(ownPeriod.updated_at));
  await expect(card.getByText(`Обновлено: ${updatedAt}`, { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
}

async function expectEmployeeIsolation(
  employeeApi: AuthenticatedApi,
  employeeId: string,
  ownPeriod: StaffPayPeriod,
  foreignPeriodId: string,
  foreignEmployeeId: string,
) {
  const ownResponse = await employeeApi.get("staff-pay/mine", {
    params: { page: 1, page_size: 25, employee_id: foreignEmployeeId },
  });
  expect(ownResponse.status()).toBe(200);
  expect(ownResponse.headers()["cache-control"]).toContain("no-store");
  const ownPeriods = (await ownResponse.json()) as StaffPayList;
  expect(ownPeriods.total).toBe(1);
  expect(ownPeriods.items.map((period) => period.id)).toEqual([ownPeriod.id]);
  expect(ownPeriods.items.every((period) => period.employee_id === employeeId)).toBe(true);
  expect(ownPeriods.items.some((period) => period.id === foreignPeriodId)).toBe(false);
  expect(
    (await employeeApi.get("staff-pay", { params: { employee_id: employeeId } })).status(),
  ).toBe(403);
  expect(
    (
      await employeeApi.post("staff-pay", {
        data: {
          operation_id: randomUUID(),
          employee_id: employeeId,
          period_from: ownPeriod.period_from,
          period_to: ownPeriod.period_to,
          amount: "1.00",
          note: "Недоступная сотруднику операция",
        },
      })
    ).status(),
  ).toBe(403);
  expect((await employeeApi.get(`staff-pay/mine/${foreignPeriodId}/entries`)).status()).toBe(404);
  expect((await employeeApi.get(`staff-pay/mine/${foreignPeriodId}/revisions`)).status()).toBe(404);

  const entriesResponse = await employeeApi.get(`staff-pay/mine/${ownPeriod.id}/entries`);
  expect(entriesResponse.status()).toBe(200);
  const entries = (await entriesResponse.json()) as PageList<StaffPayEntry>;
  expect(entries.total).toBe(3);
  const payment = entries.items.find((entry) => entry.kind === "payment");
  if (!payment) throw new Error("The test payment is unavailable in personal history");
  expect(payment.is_reversed).toBe(true);
  expect(entries.items.some((entry) => entry.reverses_entry_id === payment.id)).toBe(true);
  expect(entries.items.some((entry) => entry.kind === "advance" && !entry.is_reversed)).toBe(true);
  const revisionsResponse = await employeeApi.get(`staff-pay/mine/${ownPeriod.id}/revisions`);
  expect(revisionsResponse.status()).toBe(200);
  const revisions = (await revisionsResponse.json()) as PageList<StaffPayRevision>;
  expect(revisions.total).toBe(2);
  expect(revisions.items.map((revision) => Number(revision.amount)).sort((a, b) => a - b)).toEqual([
    1000, 1200,
  ]);
}

async function expectUiTotal(card: Locator, label: string, value: number) {
  const formatted = `${new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)} TJS`;
  await expect(card.getByText(label, { exact: true }).locator("..").locator("dd")).toHaveText(
    formatted,
  );
}

async function recordEntry(
  page: Page,
  periodId: string,
  kind: "advance" | "payment",
  amount: string,
  paidOn: string,
  note: string,
) {
  await page
    .getByRole("main")
    .getByRole("button", { name: "Записать выдачу", exact: true })
    .click();
  const form = page.getByRole("dialog", { name: "Записать выдачу", exact: true });
  await form.getByLabel("Вид выдачи", { exact: true }).selectOption(kind);
  await form.getByLabel("Дата выдачи", { exact: true }).fill(paidOn);
  await form.getByLabel("Сумма выдачи, TJS", { exact: true }).fill(amount);
  await form.getByLabel("Комментарий", { exact: true }).fill(note);
  await form.getByRole("checkbox", { name: "Наличные уже выданы сотруднику", exact: true }).check();
  const saved = waitForStaffPayResponse(page, "POST", `/staff-pay/${periodId}/entries`);
  await form.getByRole("button", { name: "Сохранить", exact: true }).click();
  const response = await saved;
  expect(response.status()).toBe(201);
  const entry = (await response.json()) as StaffPayEntry;
  expect(entry.kind).toBe(kind);
  expect(Number(entry.amount)).toBe(Number(amount));
  await expect(form).toBeHidden();
  return entry;
}

test("owner records manual staff pay while employees can reveal only their own readonly history", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const anonymous = await request.newContext();
  let ownerApi: AuthenticatedApi | null = null;
  let employee: Awaited<ReturnType<typeof inviteEmployee>> | null = null;
  let otherEmployee: Awaited<ReturnType<typeof inviteEmployee>> | null = null;
  try {
    ownerApi = await apiContext((await apiLogin(anonymous, OWNER)).access_token);
    const settingsResponse = await ownerApi.get("tenant/settings");
    expect(settingsResponse.status()).toBe(200);
    const settings = (await settingsResponse.json()) as { report_timezone: string };
    const branch = await seedBranch(ownerApi, uniqueName("Точка ручных выплат"));
    const roleResponse = await ownerApi.post("roles", {
      data: { name: uniqueName("Сотрудник без управления выплатами"), permissions: ["pos.sell"] },
    });
    expect(roleResponse.status()).toBe(201);
    const role = (await roleResponse.json()) as { id: string };
    employee = await inviteEmployee(ownerApi, role.id, branch.id, uniqueName("Сотрудник выплат"));
    otherEmployee = await inviteEmployee(
      ownerApi,
      role.id,
      branch.id,
      uniqueName("Другой сотрудник выплат"),
    );
    const paidOn = new Date().toISOString().slice(0, 10);
    const periodFrom = `${paidOn.slice(0, 7)}-01`;
    const foreignResponse = await ownerApi.post("staff-pay", {
      data: {
        employee_id: otherEmployee.id,
        period_from: periodFrom,
        period_to: paidOn,
        amount: "4000.00",
        note: "Период другого сотрудника",
        operation_id: randomUUID(),
      },
    });
    expect(foreignResponse.status()).toBe(201);
    const foreignPeriod = (await foreignResponse.json()) as StaffPayPeriod;

    await loginInBrowser(page, OWNER);
    await page.goto("/staff-pay", { waitUntil: "domcontentloaded" });
    const main = page.getByRole("main");
    await expect(
      main.getByRole("heading", { name: "Выплаты сотрудникам", exact: true }),
    ).toBeVisible();
    await main.getByLabel("Найти сотрудника", { exact: true }).fill(employee.fullName);
    const searched = waitForStaffPayResponse(page, "POST", "/staff-pay/employees/search");
    await main.getByRole("button", { name: "Найти", exact: true }).click();
    expect((await searched).status()).toBe(200);
    await main
      .getByRole("list", { name: "Выбрать сотрудника", exact: true })
      .getByRole("button", { name: employee.fullName, exact: true })
      .click();
    await main.getByRole("button", { name: "Добавить период", exact: true }).click();
    const createForm = page.getByRole("dialog", { name: "Добавить период", exact: true });
    await createForm.getByLabel("Начало периода", { exact: true }).fill(periodFrom);
    await createForm.getByLabel("Конец периода", { exact: true }).fill(paidOn);
    await createForm.getByLabel("Сумма за период, TJS", { exact: true }).fill("1000.00");
    await createForm.getByLabel("Комментарий", { exact: true }).fill("Первичная ручная сумма");
    const created = waitForStaffPayResponse(page, "POST", "/staff-pay");
    await createForm.getByRole("button", { name: "Сохранить", exact: true }).click();
    const createdResponse = await created;
    expect(createdResponse.status()).toBe(201);
    const period = (await createdResponse.json()) as StaffPayPeriod;
    expect(period.employee_id).toBe(employee.id);
    await expect(createForm).toBeHidden();

    await recordEntry(page, period.id, "advance", "200.00", paidOn, "Тестовый аванс");
    const payment = await recordEntry(
      page,
      period.id,
      "payment",
      "300.00",
      paidOn,
      "Тестовая выплата",
    );
    await expectPeriodTotals(ownerApi, employee.id, period.id, {
      amount: 1000,
      paid: 500,
      advance: 200,
      remaining: 500,
    });
    const card = main.getByRole("region", { name: "Выбранный период зарплаты", exact: true });
    await expectUiTotal(card, "Выдано наличными", 500);
    await expectUiTotal(card, "Из них авансы", 200);
    await expectUiTotal(card, "Осталось выдать", 500);
    for (const theme of ["light", "dark"]) {
      await page.evaluate(
        (value) => document.documentElement.setAttribute("data-theme", value),
        theme,
      );
      for (const width of [320, 390, 1440]) {
        await page.setViewportSize({ width, height: 844 });
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        ).toBe(true);
        await expect(
          card.getByRole("button", { name: "Записать выдачу", exact: true }),
        ).toBeVisible();
      }
    }
    const inlineHistory = main.getByRole("region", {
      name: "Выдачи за выбранный период",
      exact: true,
    });
    await expect(inlineHistory.getByText("Тестовый аванс", { exact: true })).toBeVisible();
    await expect(inlineHistory.getByText("Тестовая выплата", { exact: true })).toBeVisible();
    await expect(
      inlineHistory.getByRole("button", { name: "Отменить запись", exact: true }),
    ).toHaveCount(0);

    await card.getByRole("button", { name: "История выдач", exact: true }).click();
    const history = page.getByRole("dialog", { name: "История выдач", exact: true });
    await history
      .getByRole("list", { name: "Выдачи наличными", exact: true })
      .getByRole("listitem")
      .filter({ hasText: "Тестовая выплата" })
      .getByRole("button", { name: "Отменить запись", exact: true })
      .click();
    const reverseForm = page.getByRole("dialog", { name: "Отменить запись", exact: true });
    await reverseForm
      .getByLabel("Причина отмены", { exact: true })
      .fill("Исправление тестовой записи");
    const reversed = waitForStaffPayResponse(
      page,
      "POST",
      `/staff-pay/${period.id}/entries/${payment.id}/reverse`,
    );
    await reverseForm.getByRole("button", { name: "Подтвердить отмену", exact: true }).click();
    const reverseResponse = await reversed;
    expect(reverseResponse.status()).toBe(201);
    expect(((await reverseResponse.json()) as StaffPayEntry).reverses_entry_id).toBe(payment.id);
    await expect(reverseForm).toBeHidden();
    await expect(history.getByText("Отменена", { exact: true })).toBeVisible();
    await history.getByRole("button", { name: "Закрыть", exact: true }).click();
    await expectPeriodTotals(ownerApi, employee.id, period.id, {
      amount: 1000,
      paid: 200,
      advance: 200,
      remaining: 800,
    });
    await expectUiTotal(card, "Осталось выдать", 800);

    await card.getByRole("button", { name: "Изменить сумму", exact: true }).click();
    const editForm = page.getByRole("dialog", { name: "Изменить сумму", exact: true });
    await editForm.getByLabel("Сумма за период, TJS", { exact: true }).fill("1200.00");
    await editForm.getByLabel("Комментарий", { exact: true }).fill("Уточнённая ручная сумма");
    const updated = waitForStaffPayResponse(page, "PATCH", `/staff-pay/${period.id}`);
    await editForm.getByRole("button", { name: "Сохранить", exact: true }).click();
    expect((await updated).status()).toBe(200);
    await expect(editForm).toBeHidden();
    const finalPeriod = await expectPeriodTotals(ownerApi, employee.id, period.id, {
      amount: 1200,
      paid: 200,
      advance: 200,
      remaining: 1000,
    });
    await expectUiTotal(card, "Сумма за период", 1200);
    await expectUiTotal(card, "Осталось выдать", 1000);
    await expectEmployeeIsolation(
      employee.api,
      employee.id,
      finalPeriod,
      foreignPeriod.id,
      otherEmployee.id,
    );

    await installBrowserSession(page, employee.tokens);
    await page.setViewportSize({ width: 390, height: 844 });
    let personalRequests = 0;
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/v1/staff-pay/mine"))
        personalRequests += 1;
    });
    await page.goto("/my-work", { waitUntil: "domcontentloaded" });
    await expect(
      main.getByRole("button", { name: "Показать сведения", exact: true }),
    ).toBeVisible();
    await expect(main.getByRole("list", { name: "Периоды зарплаты", exact: true })).toHaveCount(0);
    expect(personalRequests).toBe(0);
    const personal = waitForStaffPayResponse(page, "GET", "/staff-pay/mine");
    await main.getByRole("button", { name: "Показать сведения", exact: true }).click();
    await expectPersonalStaffPay(
      page,
      await personal,
      finalPeriod,
      foreignPeriod.id,
      settings.report_timezone,
    );

    const ownCard = main
      .getByRole("list", { name: "Периоды зарплаты", exact: true })
      .getByRole("listitem");
    const ownEntries = waitForStaffPayResponse(page, "GET", `/staff-pay/mine/${period.id}/entries`);
    await ownCard.getByRole("button", { name: "История выдач", exact: true }).click();
    expect((await ownEntries).status()).toBe(200);
    const ownHistory = page.getByRole("dialog", { name: "История выдач", exact: true });
    await expect(
      ownHistory.getByRole("list", { name: "Выдачи наличными" }).getByRole("listitem"),
    ).toHaveCount(3);
    await expect(ownHistory.getByText("Отмена выдачи", { exact: true })).toBeVisible();
    await expect(
      ownHistory.getByRole("button", { name: "Отменить запись", exact: true }),
    ).toHaveCount(0);
    await ownHistory.getByRole("button", { name: "Закрыть", exact: true }).click();
    const ownRevisions = waitForStaffPayResponse(
      page,
      "GET",
      `/staff-pay/mine/${period.id}/revisions`,
    );
    await ownCard.getByRole("button", { name: "Изменения суммы", exact: true }).click();
    expect((await ownRevisions).status()).toBe(200);
    const revisions = page.getByRole("dialog", { name: "Изменения суммы", exact: true });
    await expect(
      revisions.getByRole("list", { name: "Изменения суммы за период" }).getByRole("listitem"),
    ).toHaveCount(2);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await revisions.getByRole("button", { name: "Закрыть", exact: true }).click();

    await main.getByRole("button", { name: "Скрыть сведения", exact: true }).click();
    await expect(main.getByRole("list", { name: "Периоды зарплаты", exact: true })).toHaveCount(0);
    const beforeReload = personalRequests;
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(
      main.getByRole("button", { name: "Показать сведения", exact: true }),
    ).toBeVisible();
    expect(personalRequests).toBe(beforeReload);
    const reloaded = waitForStaffPayResponse(page, "GET", "/staff-pay/mine");
    await main.getByRole("button", { name: "Показать сведения", exact: true }).click();
    await expectPersonalStaffPay(
      page,
      await reloaded,
      finalPeriod,
      foreignPeriod.id,
      settings.report_timezone,
    );
  } finally {
    await otherEmployee?.api.dispose();
    await employee?.api.dispose();
    await ownerApi?.dispose();
    await anonymous.dispose();
  }
});
