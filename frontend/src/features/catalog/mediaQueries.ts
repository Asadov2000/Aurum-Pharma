import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { deleteCatalogImage, getCatalogImage, uploadCatalogImage } from "./mediaApi";

const mediaKeys = {
  image: (id: string, version: string, variant: "display" | "thumbnail") =>
    ["catalog", "image", id, version, variant] as const,
};

export function useCatalogImageQuery(
  itemId: string,
  imageVersion: string | null | undefined,
  variant: "display" | "thumbnail",
  enabled: boolean,
) {
  return useQuery({
    queryKey: mediaKeys.image(itemId, imageVersion ?? "none", variant),
    queryFn: () => getCatalogImage(itemId, imageVersion as string, variant),
    enabled: enabled && Boolean(imageVersion),
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 10 * 60 * 1000,
    retry: 1,
  });
}

export function useUploadCatalogImage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { itemId: string; file: File }) =>
      uploadCatalogImage(args.itemId, args.file),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["catalog", "list"] });
      void qc.invalidateQueries({ queryKey: ["catalog", "item", data.id] });
      void qc.invalidateQueries({ queryKey: ["catalog", "summary"] });
    },
  });
}

export function useDeleteCatalogImage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => deleteCatalogImage(itemId),
    onSuccess: (data) => {
      void qc.removeQueries({ queryKey: ["catalog", "image", data.id] });
      void qc.invalidateQueries({ queryKey: ["catalog", "list"] });
      void qc.invalidateQueries({ queryKey: ["catalog", "item", data.id] });
      void qc.invalidateQueries({ queryKey: ["catalog", "summary"] });
    },
  });
}
