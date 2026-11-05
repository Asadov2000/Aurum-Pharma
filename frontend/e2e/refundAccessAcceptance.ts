import { type Page, type Request, type Route } from "@playwright/test";

import { type MeResponse } from "../src/features/auth/types";

import { expect } from "./helpers";

interface AccessRecoveryProbe {
  parentSaleId: string;
  receiptNumber: string;
  title: string;
  journalPrefix: string;
  reopen: () => Promise<void>;
}

/**
 * Exercise the browser's current-authority presentation contract.
 * Only /auth/me presentation is overlaid. Tokens, real server permissions,
 * financial endpoints and persisted data are never replaced or modified.
 * Real authority denial is covered independently by HTTP/DB/RLS tests.
 */
export async function verifyRefundAccessRecovery(
  page: Page,
  probe: AccessRecoveryProbe,
): Promise<void> {
  const entries = () =>
    page.evaluate(
      (prefix) => Object.entries(localStorage).filter(([key]) => key.startsWith(prefix)),
      probe.journalPrefix,
    );
  const initial = await entries();
  expect(initial).toHaveLength(1);
  const modal = page.getByRole("dialog", { name: probe.title, exact: true });
  await expect(modal.getByRole("button", { name: "Провести возврат", exact: true })).toBeEnabled();

  let mode: "readonly" | "revoked" = "readonly";
  const seen = { readonly: 0, revoked: 0 };
  const submitted: string[] = [];
  const supportId = crypto.randomUUID();
  const mePattern = "**/api/v1/auth/me";
  const observe = (request: Request) => {
    const path = new URL(request.url()).pathname;
    if (
      request.method() === "POST" &&
      path.startsWith(`/api/v1/sales/${probe.parentSaleId}/`) &&
      /\/(?:unit|mixed)-electronic-refunds\//.test(path)
    )
      submitted.push(path);
  };
  const present = async (route: Route): Promise<void> => {
    const phase = mode;
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    const identity = (await response.json()) as MeResponse;
    expect(identity.is_developer).toBe(false);
    if (!identity.active_tenant_id) throw new Error("Acceptance requires an active test tenant");
    seen[phase] += 1;
    const data: MeResponse =
      phase === "readonly"
        ? {
            ...identity,
            support_access: {
              id: supportId,
              tenant_id: identity.active_tenant_id,
              tenant_name: "Synthetic readonly presentation",
              reason: "Browser acceptance only",
              capabilities: ["sales.view", "pos.refund"],
              is_read_only: true,
              expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
            },
          }
        : {
            ...identity,
            permissions: identity.permissions.filter(
              (permission) =>
                permission !== "pos.refund" && permission !== "pos.refund_external_confirm",
            ),
            permission_scopes: Object.fromEntries(
              Object.entries(identity.permission_scopes ?? {}).filter(
                ([permission]) =>
                  permission !== "pos.refund" && permission !== "pos.refund_external_confirm",
              ),
            ),
          };
    await route.fulfill({ response, json: data });
  };

  page.on("request", observe);
  await page.route(mePattern, present);
  try {
    await page.reload();
    await probe.reopen();
    expect(seen.readonly).toBeGreaterThan(0);
    await expect(
      modal.getByRole("button", { name: "Проверить сохранённый результат", exact: true }),
    ).toBeEnabled();
    await expect(modal.getByRole("alert")).toHaveCount(0);
    // Both recovery/options and the independently fetched open shift must be
    // ready, otherwise a loading state could masquerade as readonly protection.
    await expect(modal.getByText(/Новые команды активации и проведения требуют/)).toHaveCount(0);
    await expect(
      modal.getByRole("button", { name: "Провести возврат", exact: true }),
    ).toBeDisabled();
    expect(await entries()).toEqual(initial);
    expect(submitted).toEqual([]);

    mode = "revoked";
    await page.reload();
    await page.goto("/sales");
    const filtered = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        response.request().method() === "GET" &&
        url.pathname === "/api/v1/sales" &&
        url.searchParams.get("receipt_number") === probe.receiptNumber
      );
    });
    await page.getByLabel("№ чека").fill(probe.receiptNumber);
    expect((await filtered).ok()).toBe(true);
    await page
      .getByRole("button", {
        name: `Открыть чек № ${probe.receiptNumber}`,
        exact: true,
      })
      .first()
      .click();
    const detail = page.getByRole("dialog", {
      name: `Чек № ${probe.receiptNumber}`,
      exact: true,
    });
    await expect(detail).toBeVisible();
    await expect(detail.getByRole("button", { name: "Печать чека", exact: true })).toBeEnabled();
    expect(seen.revoked).toBeGreaterThan(0);
    await expect(detail.getByRole("button", { name: "Оформить возврат", exact: true })).toHaveCount(
      0,
    );
    await expect(modal).toHaveCount(0);
    expect(await entries()).toEqual(initial);
    expect(submitted).toEqual([]);
  } finally {
    await page.unroute(mePattern, present);
    page.off("request", observe);
  }
  // Restore the real server identity before the actual payout command.
  await page.reload();
  await probe.reopen();
  await expect(modal.getByRole("button", { name: "Провести возврат", exact: true })).toBeEnabled();
  expect(await entries()).toEqual(initial);
}
