import { expect, test } from "@playwright/test";

interface WebManifestIcon {
  src: string;
  sizes: string;
  type: string;
  purpose?: string;
}

interface WebManifest {
  name: string;
  short_name: string;
  display: string;
  theme_color: string;
  icons: WebManifestIcon[];
}

test.describe("PWA metadata", () => {
  test("links and serves the install manifest", async ({ page, request }) => {
    await page.goto("/login", { waitUntil: "domcontentloaded" });

    const manifestHref = await page.locator('link[rel="manifest"]').getAttribute("href");
    expect(manifestHref).toBe("/manifest.webmanifest");

    const response = await request.get(manifestHref ?? "");
    expect(response.ok()).toBeTruthy();

    const manifest = (await response.json()) as WebManifest;
    expect(manifest.name).toBe("Aurum Pharma");
    expect(manifest.short_name).toBe("Aurum");
    expect(manifest.display).toBe("standalone");
    expect(manifest.theme_color).toBe("#0e7568");
    expect(manifest.icons).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ sizes: "192x192", type: "image/png" }),
        expect.objectContaining({ sizes: "512x512", type: "image/png" }),
        expect.objectContaining({ purpose: "maskable" }),
      ]),
    );
  });

  test("serves every install icon declared in the manifest", async ({ request }) => {
    const manifestResponse = await request.get("/manifest.webmanifest");
    expect(manifestResponse.ok()).toBeTruthy();

    const manifest = (await manifestResponse.json()) as WebManifest;
    for (const icon of manifest.icons) {
      const iconResponse = await request.get(icon.src);
      expect(iconResponse.ok(), `${icon.src} must be reachable`).toBeTruthy();
      expect(iconResponse.headers()["content-type"] ?? "").toContain(icon.type);
    }
  });

  test("keeps service worker caching away from API and document routes", async ({
    request,
  }) => {
    const response = await request.get("/sw.js");
    expect(response.ok()).toBeTruthy();

    const source = await response.text();
    expect(source).toContain('url.pathname.startsWith("/api/")');
    expect(source).toContain('url.pathname === "/sw.js"');
    expect(source).toContain("STATIC_ASSET_PATTERN.test(url.pathname)");
    expect(source).toContain("self.skipWaiting()");
    expect(source).toContain("self.clients.claim()");
    expect(source).not.toContain("navigate");
    expect(source).not.toContain("index.html");
    expect(source).not.toContain("cache.addAll");
  });
});
