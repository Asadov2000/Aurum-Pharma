import { describe, expect, it } from "vitest";

import { resolvePublicWebsiteUrl } from "@/lib/publicWebsite";

describe("resolvePublicWebsiteUrl", () => {
  it.each(["http://localhost:5173", "http://127.0.0.1:5173", "http://[::1]:5173"])(
    "links local preview only from a local app: %s",
    (origin) => expect(resolvePublicWebsiteUrl(undefined, origin)).toBe("http://127.0.0.1:4174/"),
  );

  it.each(["https://app.example.test", "https://pharmacy.example.ts.net", "http://192.168.1.10"])(
    "does not send remote users to their own localhost: %s",
    (origin) => {
      expect(resolvePublicWebsiteUrl(undefined, origin)).toBeNull();
      expect(resolvePublicWebsiteUrl("http://127.0.0.1:4174", origin)).toBeNull();
      expect(resolvePublicWebsiteUrl("https://localhost./", origin)).toBeNull();
    },
  );

  it("accepts the configured HTTPS website without deriving it from the app domain", () => {
    expect(
      resolvePublicWebsiteUrl(" https://aurum.example.test/about/ ", "https://app.example.test"),
    ).toBe("https://aurum.example.test/about/");
  });

  it.each([
    "https://127.0.0.2/",
    "https://127.255.255.254/",
    "https://2130706433/",
    "https://public.localhost/",
    "https://PUBLIC.LOCALHOST./",
    "https://[::ffff:127.0.0.2]/",
    "https://[0:0:0:0:0:ffff:7fff:fffe]/",
    "https://0.0.0.0/",
    "https://[::]/",
  ])("does not expose local or unspecified addresses to a remote user: %s", (url) => {
    expect(resolvePublicWebsiteUrl(url, "https://app.example.test")).toBeNull();
  });

  it.each([
    "javascript:alert(1)",
    "data:text/html,test",
    "//example.test",
    "/about",
    "http://example.test",
    "https://user:password@example.test",
    "https://example.test/?token=test",
    "https://example.test/#token",
    "not a URL",
  ])("rejects invalid destinations instead of falling back silently: %s", (url) => {
    expect(resolvePublicWebsiteUrl(url, "http://localhost:5173")).toBeNull();
  });
});
