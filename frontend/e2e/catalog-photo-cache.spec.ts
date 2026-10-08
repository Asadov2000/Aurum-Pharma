import { createServer } from "node:http";

import { expect, test } from "@playwright/test";

// A real loopback server is intentional: Playwright route interception disables
// the browser HTTP cache. This tests cache migration, not backend RLS or login.
for (const variant of ["display", "thumbnail"] as const) {
  test(`private catalog ${variant} bypasses old immutable cache after identity changes`, async ({
    page,
  }) => {
    const path = `/api/v1/catalog/synthetic/image/version-1/${variant}`;
    const ownerBody = "SYNTHETIC-PRIVATE-IMAGE";
    let upgraded = false;
    let imageRequests = 0;
    const server = createServer((request, response) => {
      const url = new URL(request.url ?? "/", "http://127.0.0.1");
      if (url.pathname === "/") {
        response.writeHead(200, {
          "Content-Type": "text/html",
          "Cache-Control": "no-store",
        });
        response.end("<!doctype html><title>Isolated catalog photo cache test</title>");
        return;
      }
      if (url.pathname !== path) {
        response.writeHead(404, { "Cache-Control": "no-store" });
        response.end();
        return;
      }
      imageRequests += 1;
      if (request.headers.authorization !== "Bearer synthetic-owner") {
        response.writeHead(request.headers.authorization ? 404 : 401, {
          "Cache-Control": "no-store",
        });
        response.end("Synthetic denial");
        return;
      }
      response.writeHead(200, {
        "Content-Type": "image/webp",
        "Cache-Control": upgraded ? "private, no-store" : "private, max-age=31536000, immutable",
        ...(upgraded ? { Vary: "Authorization, X-Aurum-Support-Session" } : {}),
      });
      response.end(ownerBody);
    });

    try {
      await new Promise<void>((resolve, reject) => {
        server.once("error", reject);
        server.listen(0, "127.0.0.1", resolve);
      });
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("Loopback server has no port");
      await page.goto(`http://127.0.0.1:${address.port}/`);

      // Axios' browser adapter reads authenticated images through XHR as blobs.
      // No request cache headers are added: the app's CORS policy stays intact.
      const read = (url: string, identity: "owner" | "foreign" | null) =>
        page.evaluate(
          ({ url, identity, ownerBody }) =>
            new Promise<{ status: number; containsOwnerBody: boolean }>((resolve, reject) => {
              const xhr = new XMLHttpRequest();
              xhr.open("GET", url);
              xhr.responseType = "blob";
              xhr.timeout = 10_000;
              if (identity) xhr.setRequestHeader("Authorization", `Bearer synthetic-${identity}`);
              xhr.onload = () => {
                const blob: Blob = xhr.response;
                void blob.text().then(
                  (body) => resolve({ status: xhr.status, containsOwnerBody: body === ownerBody }),
                  () => reject(new Error("Synthetic response could not be read")),
                );
              };
              xhr.onerror = () => reject(new Error("Synthetic image request failed"));
              xhr.ontimeout = () => reject(new Error("Synthetic image request timed out"));
              xhr.send();
            }),
          { url, identity, ownerBody },
        );

      expect(await read(path, "owner")).toEqual({ status: 200, containsOwnerBody: true });
      expect(imageRequests).toBe(1);
      upgraded = true;

      // Prove the warmed old cache really remains: server headers alone cannot
      // change a response the browser does not request again.
      expect(await read(path, "foreign")).toEqual({ status: 200, containsOwnerBody: true });
      expect(await read(path, null)).toEqual({ status: 200, containsOwnerBody: true });
      expect(imageRequests).toBe(1);

      const migratedPath = `${path}?media-policy=2`;
      expect(await read(migratedPath, "owner")).toEqual({ status: 200, containsOwnerBody: true });
      expect(await read(migratedPath, "foreign")).toEqual({
        status: 404,
        containsOwnerBody: false,
      });
      expect(await read(migratedPath, null)).toEqual({ status: 401, containsOwnerBody: false });
      expect(await read(migratedPath, "owner")).toEqual({ status: 200, containsOwnerBody: true });
      // Every read under the new policy reaches the server, including repeated
      // reads by the same owner. Denied responses contain no warmed owner bytes.
      expect(imageRequests).toBe(5);
    } finally {
      server.closeAllConnections();
      if (server.listening) {
        await new Promise<void>((resolve, reject) =>
          server.close((error) => (error ? reject(error) : resolve())),
        );
      }
    }
  });
}
