import { test, request, type Page } from "@playwright/test";

import {
  addPosItemToCart,
  apiContext,
  catalogSearchKey,
  apiLogin,
  clearLoginRateLimit,
  completePosSale,
  expect,
  loginInBrowser,
  OWNER,
  payPosSaleCash,
  seedAcceptedBatch,
  seedBranch,
  seedCatalogItem,
  seedRegister,
  seedSupplier,
  uniqueName,
} from "./helpers";

// Most important e2e flow: open shift → make a sale → FEFO splits across
// two batches → stage cash payment → atomic checkout → check that /batches
// reflects the consumed quantities.
test.describe("POS sale (owner)", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("adds items from desktop and physical scanners without starting payment", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1366, height: 768 });

    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);

    const branch = await seedBranch(api, uniqueName("SCAN-Branch"));
    const register = await seedRegister(api, branch.id, uniqueName("SCAN-Cash"));
    const supplier = await seedSupplier(api, uniqueName("SCAN-Supp"));
    const item = await seedCatalogItem(api, uniqueName("SCAN-Med"), "18.00");
    const barcode = `460${Date.now().toString().slice(-10)}`;
    const barcodeRes = await api.post(`catalog/${item.id}/barcodes`, {
      data: { code: barcode, code_type: "ean13" },
    });
    if (!barcodeRes.ok()) {
      throw new Error(
        `POST catalog/{id}/barcodes → ${barcodeRes.status()} ${await barcodeRes.text()}`,
      );
    }
    await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: item.id,
      qty: "3",
      purchasePrice: "12.00",
      salePrice: "18.00",
      expiresAt: isoDateInDays(90),
      batchNumber: "SCAN-A",
    });
    await apiAnon.dispose();
    await api.dispose();

    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
    await page.getByLabel("Наличные в кассе на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();
    await expect(page.locator('[data-barcode-sink="true"]')).toBeAttached();

    const quickProducts = page.getByRole("region", { name: "Быстрый выбор" });
    const currentReceipt = page.getByRole("region", { name: "Текущий чек" });
    const paymentPanel = page.getByRole("region", { name: "К оплате" });
    await expect(quickProducts).toBeVisible();
    await expect(currentReceipt).toBeVisible();
    await expect(paymentPanel).toBeVisible();
    await expect(paymentPanel.getByRole("button", { name: "Завершить продажу" })).toBeVisible();

    await expect
      .poll(
        async () => {
          const [quickBox, receiptBox, paymentBox] = await Promise.all([
            quickProducts.boundingBox(),
            currentReceipt.boundingBox(),
            paymentPanel.boundingBox(),
          ]);
          if (!quickBox || !receiptBox || !paymentBox) {
            return null;
          }

          const documentFitsViewport = await page.evaluate(() => ({
            horizontally:
              document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
            vertically:
              document.documentElement.scrollHeight <= document.documentElement.clientHeight + 1,
          }));

          return {
            columnsDoNotOverlap:
              quickBox.x + quickBox.width <= receiptBox.x &&
              receiptBox.x + receiptBox.width <= paymentBox.x,
            columnsShareTop:
              Math.abs(quickBox.y - receiptBox.y) <= 1 &&
              Math.abs(receiptBox.y - paymentBox.y) <= 1,
            documentFitsViewport,
            panelsFitViewport:
              Math.max(quickBox.y + quickBox.height, receiptBox.y + receiptBox.height) <= 768 &&
              paymentBox.y + paymentBox.height <= 768,
          };
        },
        { message: "POS workspace should settle inside the 1366x768 viewport" },
      )
      .toEqual({
        columnsDoNotOverlap: true,
        columnsShareTop: true,
        documentFitsViewport: { horizontally: true, vertically: true },
        panelsFitViewport: true,
      });

    const productSearch = page.getByRole("combobox", { name: "Товар" });
    await productSearch.fill(item.brand_name);
    await page.getByRole("button", { name: `Добавить ${item.brand_name} в избранное` }).click();
    await expect(quickProducts.getByText(item.brand_name)).toBeVisible();

    await page.reload();
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
    await expect(page.locator('[data-barcode-sink="true"]')).toBeAttached();
    const restoredQuickProducts = page.getByRole("region", { name: "Быстрый выбор" });
    await expect(restoredQuickProducts.getByText(item.brand_name)).toBeVisible();
    await restoredQuickProducts
      .getByRole("button", { name: `Убрать ${item.brand_name} из избранного` })
      .click();
    await expect(restoredQuickProducts.getByText(item.brand_name)).toHaveCount(0);

    const barcodeLookup = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/v1/catalog/by-barcode/${barcode}`) && response.ok(),
    );
    await page.evaluate((code) => {
      window.dispatchEvent(
        new CustomEvent("aurum-desktop-barcode-scanned", {
          detail: { code },
        }),
      );
    }, ` ${barcode} `);

    await barcodeLookup;
    await expect(page.getByTestId("cart-item")).toHaveCount(1, { timeout: 30_000 });
    await expect(page.getByTestId("cart-item").getByText(item.brand_name)).toBeVisible();
    await expect(page.getByText("18.00", { exact: false }).first()).toBeVisible();

    const physicalBarcodeLookup = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/v1/catalog/by-barcode/${barcode}`) && response.ok(),
    );
    let itemOperationId: string | undefined;
    let recoveredOperationId: string | undefined;
    let blockRecoveryLookup = true;
    await page.route("**/api/v1/sales/*/items", async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      const payload = route.request().postDataJSON() as { operation_id?: string };
      itemOperationId = payload.operation_id;
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      await route.abort("failed");
    });
    await page.route("**/api/v1/pos/commands/*", async (route) => {
      recoveredOperationId = route.request().url().split("/").at(-1);
      if (blockRecoveryLookup) {
        await route.abort("failed");
        return;
      }
      await route.continue();
    });
    await page.locator('[data-barcode-sink="true"]').focus();
    await page.evaluate((code) => {
      for (const key of code) {
        window.dispatchEvent(
          new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }),
        );
      }
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }),
      );
    }, barcode);

    await physicalBarcodeLookup;
    await expect(
      page.getByText("Команда не подтверждена. Проверьте связь и повторите."),
    ).toBeVisible();
    expect(itemOperationId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    expect(recoveredOperationId).toBe(itemOperationId);

    blockRecoveryLookup = false;
    await page.reload();
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
    await expect(page.getByTestId("cart-item")).toHaveCount(2, { timeout: 30_000 });
    await expect(
      page.getByText("Команда не подтверждена. Проверьте связь и повторите."),
    ).toHaveCount(0);
    expect(recoveredOperationId).toBe(itemOperationId);
    await expect(page.getByText("Оплачено 0.00", { exact: false })).toBeVisible();
    await expect(page.getByRole("button", { name: /Сбросить расчёт/i })).toHaveCount(0);

    await page.unroute("**/api/v1/sales/*/items");
    await page.unroute("**/api/v1/pos/commands/*");
  });

  test("allows only one browser tab to control a register", async ({ page }) => {
    test.setTimeout(90_000);

    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);
    const branch = await seedBranch(api, uniqueName("LOCK-Branch"));
    const register = await seedRegister(api, branch.id, uniqueName("LOCK-Cash"));
    await apiAnon.dispose();
    await api.dispose();

    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
    await page.getByLabel("Наличные в кассе на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByRole("region", { name: "Текущий чек" })).toBeVisible();

    const secondPage = await page.context().newPage();
    await secondPage.goto("/pos");
    await secondPage.getByLabel(/^Касса$/).selectOption({ label: register.name });

    await expect(secondPage.getByText("Касса занята", { exact: true })).toBeVisible();
    await expect(secondPage.getByRole("region", { name: "Текущий чек" })).toHaveCount(0);

    await page.close();

    await expect(secondPage.getByRole("region", { name: "Текущий чек" })).toBeVisible({
      timeout: 10_000,
    });
    await expect(secondPage.getByText("Касса занята", { exact: true })).toHaveCount(0);
    await secondPage.close();
  });

  test("binds card sale and refund to confirmed terminal attempts", async ({ page }) => {
    test.setTimeout(120_000);

    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);

    const branch = await seedBranch(api, uniqueName("CARD-Branch"));
    const register = await seedRegister(api, branch.id, uniqueName("CARD-Cash"));
    const terminalId = `E2E-TERM-${Date.now()}`;
    const terminalUpdate = await api.patch(`registers/${register.id}`, {
      data: { card_terminal_id: terminalId },
    });
    if (!terminalUpdate.ok()) {
      throw new Error(
        `PATCH registers/{id} → ${terminalUpdate.status()} ${await terminalUpdate.text()}`,
      );
    }
    const supplier = await seedSupplier(api, uniqueName("CARD-Supp"));
    const item = await seedCatalogItem(api, uniqueName("CARD-Med"), "24.00");
    await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: item.id,
      qty: "2",
      purchasePrice: "18.00",
      salePrice: "24.00",
      expiresAt: isoDateInDays(120),
      batchNumber: "CARD-A",
    });

    try {
      await loginInBrowser(page, OWNER);
      await page.goto("/pos");
      await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
      await page.getByLabel("Наличные в кассе на начало смены").fill("100");
      await page.getByRole("button", { name: "Открыть смену" }).click();
      await expect(page.getByText("Смена открыта")).toBeVisible();

      await addPosItemToCart(page, {
        brandName: item.brand_name,
        qty: "1",
        expectedCartItems: 1,
        searchKey: catalogSearchKey(item.brand_name),
      });

      const createAttemptResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          /\/api\/v1\/pos\/payment-attempts$/.test(response.url()) &&
          response.status() === 201,
      );
      const reconcileAttemptResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          /\/api\/v1\/pos\/payment-attempts\/[^/]+\/reconciliation$/.test(response.url()) &&
          response.ok(),
      );
      await page.getByRole("button", { name: "Карта", exact: true }).click();
      await page.getByRole("button", { name: "Перейти к оплате картой" }).click();
      const amountDialog = page.getByRole("dialog", { name: "Сумма оплаты" });
      await expect(amountDialog).toBeVisible();
      await amountDialog.getByRole("button", { name: "ОК" }).click();
      const createdAttempt = (await (await createAttemptResponse).json()) as {
        id: string;
        status: string;
      };
      expect(createdAttempt.status).toBe("pending");
      const reconciledAttemptResponse = await reconcileAttemptResponse;
      expect(reconciledAttemptResponse.url()).toContain(
        `/api/v1/pos/payment-attempts/${createdAttempt.id}/reconciliation`,
      );
      expect(((await reconciledAttemptResponse.json()) as { status: string }).status).toBe(
        "requires_reconciliation",
      );

      const confirmAttemptResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url().includes(`/api/v1/pos/payment-attempts/${createdAttempt.id}/confirm`) &&
          response.ok(),
      );
      const paymentReference = `E2E-SALE-${Date.now()}`;
      const confirmation = page.getByRole("dialog", { name: "Сверка оплаты картой" });
      await expect(confirmation.getByLabel("Терминал")).toHaveValue(terminalId);
      await confirmation.getByLabel("Номер операции/документа").fill(paymentReference);
      await confirmation.getByRole("button", { name: "Оплата прошла" }).click();
      const confirmedAttemptResponse = await confirmAttemptResponse;
      expect(confirmedAttemptResponse.request().postDataJSON()).toEqual({
        terminal_id: terminalId,
        external_reference: paymentReference,
      });
      expect(((await confirmedAttemptResponse.json()) as { status: string }).status).toBe(
        "confirmed",
      );
      await expect(page.getByText("Оплачено 24.00", { exact: false })).toBeVisible();

      const checkoutRequest = page.waitForRequest(
        (request) =>
          request.method() === "POST" && request.url().endsWith("/api/v1/sales/checkout"),
      );
      const checkoutResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url().endsWith("/api/v1/sales/checkout") &&
          response.ok(),
      );
      await completePosSale(page);
      const completedSale = (await (await checkoutResponse).json()) as {
        sale_id: string;
        receipt_number: string;
      };
      const checkoutPayload = (await checkoutRequest).postDataJSON() as {
        payments: { payment_method: string; payment_attempt_id?: string }[];
      };
      expect(checkoutPayload.payments).toEqual([
        {
          payment_method: "card",
          amount: "24.00",
          payment_attempt_id: createdAttempt.id,
        },
      ]);

      const storedAttempt = await api.get(`pos/payment-attempts/${createdAttempt.id}`);
      expect(storedAttempt.ok()).toBe(true);
      expect(((await storedAttempt.json()) as { status: string }).status).toBe("consumed");

      await page.goto("/sales");
      const filteredSalesResponse = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          response.request().method() === "GET" &&
          url.pathname.endsWith("/api/v1/sales") &&
          url.searchParams.get("receipt_number") === completedSale.receipt_number
        );
      });
      await page.getByLabel("№ чека").fill(completedSale.receipt_number);
      const filteredSales = (await (await filteredSalesResponse).json()) as {
        items: { id: string }[];
      };
      const saleRowIndex = filteredSales.items.findIndex(
        (sale) => sale.id === completedSale.sale_id,
      );
      expect(saleRowIndex).toBeGreaterThanOrEqual(0);
      const saleRow = page
        .getByRole("button", {
          name: `Открыть чек № ${completedSale.receipt_number}`,
        })
        .nth(saleRowIndex);
      await expect(saleRow).toBeVisible();
      await saleRow.click();
      const saleDialog = page.getByRole("dialog", {
        name: `Чек № ${completedSale.receipt_number}`,
      });
      await saleDialog.getByRole("button", { name: "Оформить возврат" }).click();

      let refundDialog = page.getByRole("dialog", {
        name: `Возврат по чеку № ${completedSale.receipt_number}`,
      });
      await refundDialog.getByRole("checkbox").first().check();
      const createRefundAttemptResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url().endsWith(`/api/v1/sales/${completedSale.sale_id}/refund-attempts`) &&
          response.status() === 201,
      );
      const reconcileRefundAttemptResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          /\/api\/v1\/pos\/refund-attempts\/[^/]+\/reconciliation$/.test(response.url()) &&
          response.ok(),
      );
      await refundDialog.getByLabel("Причина").selectOption("quality_issue");
      await refundDialog.getByRole("button", { name: "Зафиксировать сумму возврата" }).click();
      const refundAttempt = (await (await createRefundAttemptResponse).json()) as {
        id: string;
        status: string;
      };
      expect(refundAttempt.status).toBe("pending");
      const reconciledRefundAttemptResponse = await reconcileRefundAttemptResponse;
      expect(reconciledRefundAttemptResponse.url()).toContain(
        `/api/v1/pos/refund-attempts/${refundAttempt.id}/reconciliation`,
      );
      expect(((await reconciledRefundAttemptResponse.json()) as { status: string }).status).toBe(
        "requires_reconciliation",
      );

      await page.evaluate(
        (saleId) => window.localStorage.removeItem(`sales:pendingRefund:${saleId}`),
        completedSale.sale_id,
      );
      await page.reload();
      const recoveredSalesResponse = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          response.request().method() === "GET" &&
          url.pathname.endsWith("/api/v1/sales") &&
          url.searchParams.get("receipt_number") === completedSale.receipt_number
        );
      });
      await page.getByLabel("№ чека").fill(completedSale.receipt_number);
      const recoveredSales = (await (await recoveredSalesResponse).json()) as {
        items: { id: string }[];
      };
      const recoveredSaleIndex = recoveredSales.items.findIndex(
        (sale) => sale.id === completedSale.sale_id,
      );
      expect(recoveredSaleIndex).toBeGreaterThanOrEqual(0);
      await page
        .getByRole("button", { name: `Открыть чек № ${completedSale.receipt_number}` })
        .nth(recoveredSaleIndex)
        .click();
      await page
        .getByRole("dialog", { name: `Чек № ${completedSale.receipt_number}` })
        .getByRole("button", { name: "Оформить возврат" })
        .click();
      refundDialog = page.getByRole("dialog", {
        name: `Возврат по чеку № ${completedSale.receipt_number}`,
      });
      await expect(
        refundDialog.getByText(
          "Найдена заявка, требующая сверки. Не повторяйте возврат во внешнем терминале; проверьте его документ.",
        ),
      ).toBeVisible();

      const refundDocument = `E2E-REFUND-${Date.now()}`;
      await refundDialog.getByLabel("Терминал").fill("E2E-TERM-01");
      await refundDialog.getByLabel("Номер документа").fill(refundDocument);
      const confirmRefundAttemptResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url().endsWith(`/api/v1/pos/refund-attempts/${refundAttempt.id}/confirm`) &&
          response.ok(),
      );
      const refundResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url().endsWith(`/api/v1/sales/${completedSale.sale_id}/refund`) &&
          response.ok(),
      );
      const refundRequest = page.waitForRequest(
        (request) =>
          request.method() === "POST" &&
          request.url().endsWith(`/api/v1/sales/${completedSale.sale_id}/refund`),
      );
      await refundDialog.getByRole("button", { name: "Подтвердить возврат и создать чек" }).click();
      expect(
        ((await (await confirmRefundAttemptResponse).json()) as { status: string }).status,
      ).toBe("confirmed");
      const returnedSale = (await (await refundResponse).json()) as {
        id: string;
        refund_attempt_id: string | null;
        sale_type: string;
      };
      expect(returnedSale.sale_type).toBe("return");
      expect(returnedSale.refund_attempt_id).toBe(refundAttempt.id);
      expect((await refundRequest).postDataJSON()).toMatchObject({
        reason: "quality_issue",
        comment: null,
      });

      const storedRefundAttempt = await api.get(`pos/refund-attempts/${refundAttempt.id}`);
      expect(storedRefundAttempt.ok()).toBe(true);
      expect(((await storedRefundAttempt.json()) as { status: string }).status).toBe("consumed");

      await expect(page.getByText("Чек возврата", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "Печать возврата" }).click();
      await expect(page.getByRole("dialog", { name: "Печать возврата" })).toBeVisible();
      await expect(page.getByText("ВОЗВРАТ", { exact: true })).toBeVisible();
      await expect(
        page.getByText(`Исходный чек № ${completedSale.receipt_number}`, { exact: true }),
      ).toBeVisible();
      await expect(page.getByText("ВОЗВРАЩЕНО", { exact: true })).toBeVisible();
      await expect(page.getByText("Принято", { exact: true })).toHaveCount(0);
      await expect(page.getByText("Сдача", { exact: true })).toHaveCount(0);
      await expect(page.getByText("Спасибо за покупку!", { exact: true })).toHaveCount(0);
    } finally {
      await apiAnon.dispose();
      await api.dispose();
    }
  });

  test("FEFO splits a 7-unit sale across two batches of 5 + 5 and completes", async ({ page }) => {
    // Heaviest spec: seeds two accepted batches (~16 API calls) then drives
    // the whole sale UI. 60s is tight when the entire suite is hammering the
    // stack sequentially — give it headroom.
    test.setTimeout(120_000);
    // ---- API seed ----
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);

    const branch = await seedBranch(api, uniqueName("POS-Branch"));
    const register = await seedRegister(api, branch.id, uniqueName("POS-Cash"));
    const supplier = await seedSupplier(api, uniqueName("POS-Supp"));
    const item = await seedCatalogItem(api, uniqueName("POS-Med"), "20.00");

    // Two batches, both qty=5, different expiries — FEFO must pull from
    // the earlier-expiring batch first.
    const sooner = isoDateInDays(30);
    const later = isoDateInDays(180);
    await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: item.id,
      qty: "5",
      purchasePrice: "15.00",
      salePrice: "20.00",
      expiresAt: sooner,
      batchNumber: "FEFO-A",
    });
    await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: item.id,
      qty: "5",
      purchasePrice: "15.00",
      salePrice: "20.00",
      expiresAt: later,
      batchNumber: "FEFO-B",
    });
    await apiAnon.dispose();
    await api.dispose();

    // ---- UI ----
    await installDesktopCashDrawerBridge(page);
    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });

    // Open shift with 100 TJS in the till.
    await page.getByLabel("Наличные в кассе на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();

    // The redesigned register shows the search directly (the draft is created
    // lazily on the first add — no "+ Новая продажа" step up front).
    // Pick the catalog item and ask for 7 units → FEFO splits 5 + 2.
    const searchKey = catalogSearchKey(item.brand_name);
    await addPosItemToCart(page, {
      brandName: item.brand_name,
      qty: "7",
      expectedCartItems: 2,
      searchKey,
    });

    // Two items × 20 TJS each line = 140 TJS total to settle.
    await expect(page.getByText(/К оплате/)).toBeVisible();
    await expect(page.getByText("140.00", { exact: false }).first()).toBeVisible();

    // The tender is staged only in memory. Commit the complete sale command,
    // then lose both its HTTP response and the first recovery lookup. The POS
    // must stop in a safe uncertain state until a reload can reconcile the
    // exact operation without another checkout POST.
    await payPosSaleCash(page, "140.00");
    let checkoutRequests = 0;
    let recoveryRequests = 0;
    let blockRecoveryLookup = true;
    let checkoutOperationId: string | undefined;
    const recoveredOperationIds: string[] = [];
    await page.route("**/api/v1/sales/operations/**", async (route) => {
      recoveryRequests += 1;
      recoveredOperationIds.push(route.request().url().split("/").at(-1) ?? "");
      if (blockRecoveryLookup) {
        await route.abort("failed");
        return;
      }
      await route.continue();
    });
    await page.route("**/api/v1/sales/checkout", async (route) => {
      checkoutRequests += 1;
      const payload = route.request().postDataJSON() as { operation_id?: string };
      checkoutOperationId = payload.operation_id;
      const committed = await route.fetch();
      expect(committed.ok()).toBe(true);
      await route.abort("failed");
    });

    const completeSale = page.getByRole("button", { name: /Завершить продажу/ });
    await completeSale.click();
    await expect(
      page.getByText(
        "Не удалось проверить результат продажи. Не повторяйте оплату, пока сверка с сервером не завершится.",
      ),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Сверить с сервером" })).toBeVisible();
    expect(checkoutRequests).toBe(1);
    expect(recoveryRequests).toBe(1);
    expect(recoveredOperationIds).toEqual([checkoutOperationId]);
    await expectNoDesktopCashDrawerOpen(page);

    blockRecoveryLookup = false;
    await page.reload();
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
    await expect(page.getByText(/оформлен/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: /Печать чека/ })).toBeVisible();
    await expect.poll(() => recoveryRequests).toBe(2);

    await page.unroute("**/api/v1/sales/checkout");
    await page.unroute("**/api/v1/sales/operations/**");
    expect(checkoutRequests).toBe(1);
    expect(recoveredOperationIds).toEqual([checkoutOperationId, checkoutOperationId]);
    expect(checkoutOperationId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    await expectNoDesktopCashDrawerOpen(page);

    // ---- Print: open the receipt view and verify the totals match ----
    await page.getByRole("button", { name: /Печать чека/ }).click();
    const receipt = page.locator(".receipt-print");
    await expect(receipt).toBeVisible({ timeout: 15_000 });
    await expect(receipt.getByText("КАССОВЫЙ ЧЕК")).toBeVisible();
    // FEFO split the 7 units into two lines, so the name appears twice.
    await expect(receipt.getByText(new RegExp(item.brand_name)).first()).toBeVisible();
    // ИТОГО line carries the 140.00 total.
    await expect(receipt.getByText("ИТОГО")).toBeVisible();
    await expect(receipt.getByText(/140\.00/).first()).toBeVisible();
    // Width selector persists per device/register.
    await page.getByLabel("Ширина чека").selectOption("58");
    await page.getByRole("button", { name: "Закрыть", exact: true }).click();

    // ---- Check that batches are drained: total qty_remaining = 10 - 7 = 3 ----
    await page.goto("/batches");
    await page.getByRole("button", { name: /^Фильтры/ }).click();
    const batchCatalogPicker = page.getByPlaceholder("Найти товар…");
    await expect(batchCatalogPicker).toBeVisible({ timeout: 30_000 });
    await batchCatalogPicker.fill(searchKey);
    const batchCatalogOption = page.getByRole("option", {
      name: new RegExp(item.brand_name),
    });
    await expect(batchCatalogOption).toBeVisible({ timeout: 30_000 });
    await batchCatalogOption.click();
    // FEFO drained FEFO-A entirely (qty → 0); the page hides empty batches
    // by default, so include empty batches in the same filter panel.
    // Switch UI hides the real <input> behind a styled span — Playwright's
    // visibility check refuses to click it without force.
    await page.getByLabel(/Показывать пустые партии/).check({ force: true });
    await page
      .getByRole("dialog", { name: "Фильтры", exact: true })
      .getByRole("button", { name: "Готово" })
      .click();
    const tbody = page.locator("table tbody");
    await expect(tbody.locator("text=FEFO-A")).toBeVisible({ timeout: 15_000 });
    await expect(tbody.locator("text=FEFO-B")).toBeVisible();
  });
});

function isoDateInDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

type DesktopMessage = {
  readonly type?: string;
  readonly payload?: {
    readonly reason?: string;
    readonly registerId?: string;
    readonly saleId?: string;
  };
};

async function installDesktopCashDrawerBridge(page: Page): Promise<void> {
  await page.addInitScript(() => {
    type DesktopMessage = {
      readonly type?: string;
      readonly payload?: {
        readonly reason?: string;
        readonly registerId?: string;
        readonly saleId?: string;
      };
    };
    type DesktopTarget = Window & {
      __aurumDesktopMessages?: DesktopMessage[];
      aurumDesktop?: {
        readonly appVersion: string;
        readonly capabilities: readonly string[];
        readonly platform: "windows";
        postMessage(message: DesktopMessage): void;
      };
    };

    const target = window as DesktopTarget;
    target.__aurumDesktopMessages = [];
    target.aurumDesktop = {
      appVersion: "0.1.0-e2e",
      capabilities: ["cash-drawer"],
      platform: "windows",
      postMessage(message) {
        target.__aurumDesktopMessages?.push(message);
      },
    };
  });
}

async function expectNoDesktopCashDrawerOpen(page: Page): Promise<void> {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const target = window as Window & {
          readonly __aurumDesktopMessages?: DesktopMessage[];
        };

        return (target.__aurumDesktopMessages ?? []).filter(
          (message) => message.type === "aurum.cash-drawer.open",
        ).length;
      }),
    )
    .toBe(0);
}
