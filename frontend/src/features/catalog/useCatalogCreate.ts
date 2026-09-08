import { useRef, useState } from "react";
import { isAxiosError } from "axios";

import { createOperationId } from "@/lib/operationId";
import { useCreateCatalogItem } from "./queries";
import { type CatalogItemCreatePayload } from "./types";

/** Keep one operation and its exact request until the server resolves the outcome. */
export function useCatalogCreate() {
  const create = useCreateCatalogItem();
  const [operationId] = useState(createOperationId);
  const [uncertain, setUncertain] = useState(false);
  const request = useRef<CatalogItemCreatePayload | null>(null);

  async function submit(payload: CatalogItemCreatePayload) {
    request.current ??= { ...payload, operation_id: operationId };
    try {
      const item = await create.mutateAsync(request.current);
      setUncertain(false);
      return item;
    } catch (error) {
      const status = isAxiosError(error) ? error.response?.status : undefined;
      if (status === 409 || status === 422) {
        request.current = null;
        setUncertain(false);
      } else {
        setUncertain(true);
      }
      throw error;
    }
  }

  return { submit, uncertain, pending: create.isPending };
}
