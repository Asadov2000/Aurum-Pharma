import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

export type CatalogFieldTemplateKind = "category" | "dosage" | "pack_size";

export interface CatalogFieldTemplate {
  id: string;
  kind: CatalogFieldTemplateKind;
  value: string;
}

const templatesKey = ["catalog", "field-templates"] as const;

async function listFieldTemplates(signal?: AbortSignal): Promise<CatalogFieldTemplate[]> {
  const { data } = await api.get<CatalogFieldTemplate[]>("/catalog/field-templates", { signal });
  return data;
}

async function createFieldTemplate(input: {
  kind: CatalogFieldTemplateKind;
  value: string;
}): Promise<CatalogFieldTemplate> {
  const { data } = await api.post<CatalogFieldTemplate>("/catalog/field-templates", input);
  return data;
}

async function deleteFieldTemplate(id: string): Promise<void> {
  await api.delete(`/catalog/field-templates/${id}`);
}

export function useFieldTemplates(tenantId: string | null | undefined, enabled: boolean) {
  return useQuery({
    queryKey: [...templatesKey, tenantId],
    queryFn: ({ signal }) => listFieldTemplates(signal),
    staleTime: 60_000,
    enabled: enabled && Boolean(tenantId),
  });
}

export function useCreateFieldTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: createFieldTemplate,
    onSuccess: () => void client.invalidateQueries({ queryKey: templatesKey }),
  });
}

export function useDeleteFieldTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: deleteFieldTemplate,
    onSuccess: () => void client.invalidateQueries({ queryKey: templatesKey }),
  });
}
