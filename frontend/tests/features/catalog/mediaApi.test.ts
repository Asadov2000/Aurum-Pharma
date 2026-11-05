import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api", () => ({ api }));

import { getCatalogImage } from "@/features/catalog/mediaApi";

describe("catalog image cache policy migration", () => {
  beforeEach(() => api.get.mockReset());

  it.each(["display", "thumbnail"] as const)(
    "%s uses a new public cache key and preserves the authenticated blob reader",
    async (variant) => {
      const image = new Blob(["synthetic image"], { type: "image/webp" });
      api.get.mockResolvedValue({ data: image });

      expect(await getCatalogImage("item-1", "image-version-1", variant)).toBe(image);
      expect(api.get).toHaveBeenCalledExactlyOnceWith(
        `/catalog/item-1/image/image-version-1/${variant}`,
        { responseType: "blob", params: { "media-policy": 2 } },
      );
    },
  );
});
