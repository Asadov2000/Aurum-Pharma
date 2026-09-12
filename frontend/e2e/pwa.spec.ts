import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";

const manifestPath = "/manifest.webmanifest?v=12-8";

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
    expect(manifestHref).toBe(manifestPath);
    await expect(page.locator('link[rel="icon"]')).toHaveAttribute(
      "href",
      "/icons/favicon-12-8.svg",
    );
    const appleIcon = page.locator('link[rel="apple-touch-icon"]');
    await expect(appleIcon).toHaveAttribute("sizes", "180x180");
    const appleResponse = await request.get((await appleIcon.getAttribute("href")) ?? "");
    expect(appleResponse.ok()).toBeTruthy();
    expect(appleResponse.headers()["content-type"]).toContain("image/png");
    const applePng = await appleResponse.body();
    expect([applePng.readUInt32BE(16), applePng.readUInt32BE(20)]).toEqual([180, 180]);

    const response = await request.get(manifestHref ?? "");
    expect(response.ok()).toBeTruthy();

    const manifest = (await response.json()) as WebManifest;
    expect(manifest.name).toBe("Aurum Pharma");
    expect(manifest.short_name).toBe("Aurum");
    expect(manifest.display).toBe("standalone");
    expect(manifest.theme_color).toBe("#00566B");
    expect(manifest.icons).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ sizes: "192x192", type: "image/png" }),
        expect.objectContaining({ sizes: "512x512", type: "image/png" }),
        expect.objectContaining({ purpose: "maskable" }),
      ]),
    );
  });

  test("serves every install icon declared in the manifest", async ({ request }) => {
    const manifestResponse = await request.get(manifestPath);
    expect(manifestResponse.ok()).toBeTruthy();

    const manifest = (await manifestResponse.json()) as WebManifest;
    for (const icon of manifest.icons) {
      const iconResponse = await request.get(icon.src);
      expect(iconResponse.ok(), `${icon.src} must be reachable`).toBeTruthy();
      expect(iconResponse.headers()["content-type"] ?? "").toContain(icon.type);
      if (icon.type === "image/png") {
        const png = await iconResponse.body();
        expect(`${png.readUInt32BE(16)}x${png.readUInt32BE(20)}`).toBe(icon.sizes);
      }
    }
  });

  test("keeps maskable artwork inside the circular safe zone", async ({ page, request }) => {
    const manifestResponse = await request.get(manifestPath);
    const manifest = (await manifestResponse.json()) as WebManifest;
    const maskableIcon = manifest.icons.find((icon) => icon.purpose === "maskable");
    expect(maskableIcon).toBeDefined();
    // A same-origin static document avoids login and needs no database fixture.
    await page.goto(manifestPath);
    const coverage = await page.evaluate(async (src) => {
      const icon = new Image();
      icon.src = src;
      await icon.decode();
      const canvas = document.createElement("canvas");
      canvas.width = icon.naturalWidth;
      canvas.height = icon.naturalHeight;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Canvas is unavailable");
      context.drawImage(icon, 0, 0);
      const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
      const radius = Math.min(canvas.width, canvas.height) * 0.4;
      let outsideArtwork = 0;
      let transparentPixels = 0;
      for (let y = 0; y < canvas.height; y += 1) {
        for (let x = 0; x < canvas.width; x += 1) {
          const offset = (y * canvas.width + x) * 4;
          if (data[offset + 3] !== 255) transparentPixels += 1;
          const outside =
            Math.hypot(x + 0.5 - canvas.width / 2, y + 0.5 - canvas.height / 2) > radius;
          if (outside && [0, 1, 2, 3].some((channel) => data[offset + channel] !== data[channel])) {
            outsideArtwork += 1;
          }
        }
      }
      return { outsideArtwork, transparentPixels };
    }, maskableIcon?.src ?? "");
    expect(coverage).toEqual({ outsideArtwork: 0, transparentPixels: 0 });
  });

  test("replaces cached old branding when the new service worker activates", async ({
    browser,
    baseURL,
    request,
  }) => {
    const iconPath = "/icons/icon-192.png?v=12-8";
    const expectedResponse = await request.get(iconPath);
    expect(expectedResponse.ok()).toBeTruthy();
    const expectedHash = createHash("sha256")
      .update(await expectedResponse.body())
      .digest("hex");
    // Keep cache/SW mutations out of other tests and existing browser profiles.
    const isolatedContext = await browser.newContext({ baseURL, serviceWorkers: "allow" });
    try {
      const page = await isolatedContext.newPage();
      await page.goto(manifestPath);
      await page.evaluate(async (src) => {
        const oldCache = await caches.open("aurum-pharma-static-v2");
        await oldCache.put(src, new Response("stale-brand-icon"));
      }, iconPath);
      expect(await page.evaluate(() => caches.keys())).toContain("aurum-pharma-static-v2");
      await page.evaluate(async () => {
        await navigator.serviceWorker.register("/sw.js");
        await navigator.serviceWorker.ready;
      });
      await expect
        .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
        .toBe(true);
      await expect
        .poll(() => page.evaluate(() => caches.keys()))
        .not.toContain("aurum-pharma-static-v2");
      const actualHash = await page.evaluate(async (src) => {
        const response = await fetch(src);
        const digest = await crypto.subtle.digest("SHA-256", await response.arrayBuffer());
        return Array.from(new Uint8Array(digest), (byte) =>
          byte.toString(16).padStart(2, "0"),
        ).join("");
      }, iconPath);
      expect(actualHash).toBe(expectedHash);
      await expect
        .poll(() => page.evaluate(() => caches.keys()))
        .toContain("aurum-pharma-static-v3-brand12-8");
    } finally {
      await isolatedContext.close();
    }
  });

  test("keeps service worker caching away from API and document routes", async ({ request }) => {
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
