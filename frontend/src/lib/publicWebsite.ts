const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

function isLoopback(url: URL): boolean {
  return LOOPBACK_HOSTS.has(url.hostname.replace(/\.$/, ""));
}

/** Only deployment configuration chooses this destination, never login/query data. */
export function resolvePublicWebsiteUrl(
  configuredUrl: string | undefined,
  pageOrigin: string | undefined = typeof window === "undefined"
    ? undefined
    : window.location.origin,
): string | null {
  let localPage: boolean;
  try {
    localPage = pageOrigin !== undefined && isLoopback(new URL(pageOrigin));
  } catch {
    return null;
  }

  const configured = configuredUrl?.trim();
  if (!configured) return localPage ? "http://127.0.0.1:4174/" : null;

  try {
    const url = new URL(configured);
    if (url.username || url.password || url.search || url.hash) return null;
    if (isLoopback(url) && !localPage) return null;
    if (url.protocol !== "https:" && !(url.protocol === "http:" && isLoopback(url) && localPage)) {
      return null;
    }
    return url.href;
  } catch {
    return null;
  }
}
