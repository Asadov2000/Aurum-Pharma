const CACHE_NAME = "aurum-pharma-static-v2";
const MAX_STATIC_CACHE_ENTRIES = 80;
const STATIC_ASSET_PATTERN = /\.(?:css|ico|js|png|svg|webmanifest|woff2?)$/i;

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== CACHE_NAME)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);
  if (
    url.origin !== self.location.origin ||
    url.pathname === "/sw.js" ||
    url.pathname.startsWith("/api/") ||
    !STATIC_ASSET_PATTERN.test(url.pathname)
  ) {
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) {
        return cached;
      }

      return fetch(request).then((response) => {
        if (response.status === 200 && response.type === "basic") {
          const responseCopy = response.clone();
          event.waitUntil(
            caches
              .open(CACHE_NAME)
              .then(async (cache) => {
                await cache.put(request, responseCopy);
                const keys = await cache.keys();
                const staleKeys = keys.slice(0, Math.max(0, keys.length - MAX_STATIC_CACHE_ENTRIES));
                await Promise.all(staleKeys.map((key) => cache.delete(key)));
              })
              .catch(() => undefined),
          );
        }

        return response;
      });
    }),
  );
});
