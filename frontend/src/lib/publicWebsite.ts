function isLoopback(url: URL): boolean {
  const hostname = url.hostname.replace(/\.$/, "");
  // URL normalizes alternative IPv4 notation and compresses mapped IPv6 addresses.
  return (
    hostname === "localhost" ||
    hostname.endsWith(".localhost") ||
    hostname === "[::1]" ||
    /^127(?:\.\d{1,3}){3}$/.test(hostname) ||
    /^\[::ffff:7f[0-9a-f]{2}:[0-9a-f]{1,4}\]$/.test(hostname)
  );
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
    if (url.hostname === "0.0.0.0" || url.hostname === "[::]") return null;
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
