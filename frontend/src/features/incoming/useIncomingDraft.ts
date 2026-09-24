import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";

import { createOperationId } from "@/lib/operationId";
import { commitIncomingDraftRow, listIncomingDraftRows, saveIncomingDraftRow } from "./api";
import { incomingKeys } from "./queries";
import {
  type IncomingDraftFields,
  type IncomingDraftRow,
  type IncomingDraftSavePayload,
} from "./types";

export const EMPTY_DRAFT_FIELDS: IncomingDraftFields = {
  source_name: "",
  catalog_id: "",
  batch_number: "",
  manufactured_at: "",
  expires_at: "",
  qty: "",
  purchase_price: "",
  sale_price: "",
};
export const DRAFT_SAVE_DELAY = 600;
type Phase = "idle" | "unsaved" | "saving" | "saved" | "error" | "conflict";

export function draftFields(row?: IncomingDraftRow): IncomingDraftFields {
  return row
    ? {
        ...row.fields,
        source_name: row.fields.source_name ?? "",
        purchase_price: row.fields.purchase_price ?? "",
      }
    : { ...EMPTY_DRAFT_FIELDS };
}

export function useIncomingDraft(
  documentId: string,
  row: IncomingDraftRow | undefined,
  enabled: boolean,
) {
  const queryClient = useQueryClient();
  const [rowId] = useState(() => row?.id ?? createOperationId());
  const [phase, setPhase] = useState<Phase>(row ? "saved" : "idle");
  const [commitPending, setCommitPending] = useState(false);
  const [commitUncertain, setCommitUncertain] = useState(false);
  const latest = useRef(draftFields(row));
  const saved = useRef(JSON.stringify(latest.current));
  const revision = useRef(row?.revision ?? 0);
  const pendingSave = useRef<IncomingDraftSavePayload | null>(null);
  const inFlight = useRef<Promise<number> | null>(null);
  const commitRevision = useRef<number | null>(null);
  const conflicted = useRef(false);
  const mounted = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      clearTimeout(timer.current);
      // An HTTP mutation may already have committed: never pretend abort is rollback.
    };
  }, []);

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: incomingKeys.drafts(documentId) });
  }, [documentId, queryClient]);

  const save = useCallback((): Promise<number> => {
    clearTimeout(timer.current);
    if (inFlight.current) return inFlight.current;
    if (conflicted.current) return Promise.reject(new Error("Draft revision conflict"));
    if (!enabled || commitRevision.current !== null) return Promise.resolve(revision.current);
    const task = async () => {
      while (pendingSave.current || saved.current !== JSON.stringify(latest.current)) {
        if (mounted.current) setPhase("saving");
        // A lost reply is retried with the SAME revision and snapshot before newer edits.
        const payload = pendingSave.current ?? {
          expected_revision: revision.current,
          fields: { ...latest.current },
        };
        pendingSave.current = payload;
        try {
          const response = await saveIncomingDraftRow(documentId, rowId, payload);
          revision.current = response.revision;
          saved.current = JSON.stringify(payload.fields);
          pendingSave.current = null;
          invalidate();
        } catch (error) {
          const status = isAxiosError(error) ? error.response?.status : undefined;
          conflicted.current = status === 404 || status === 409;
          // Auth/rate-limit responses say nothing about an earlier lost successful PUT.
          if (status === 422) {
            pendingSave.current = null;
          }
          if (mounted.current) setPhase(conflicted.current ? "conflict" : "error");
          throw error;
        }
        if (!mounted.current) return revision.current;
      }
      if (mounted.current) setPhase(revision.current > 0 ? "saved" : "idle");
      return revision.current;
    };
    const request = task().finally(() => {
      inFlight.current = null;
    });
    inFlight.current = request;
    return request;
  }, [documentId, enabled, invalidate, rowId]);

  const update = useCallback(
    (fields: IncomingDraftFields) => {
      if (!enabled || JSON.stringify(fields) === JSON.stringify(latest.current)) return;
      latest.current = fields;
      if (conflicted.current || commitRevision.current !== null) return;
      setPhase("unsaved");
      clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        void save().catch(() => undefined);
      }, DRAFT_SAVE_DELAY);
    },
    [enabled, save],
  );

  const commit = useCallback(async () => {
    if (conflicted.current) throw new Error("Draft revision conflict");
    setCommitPending(true);
    try {
      const confirmed = commitRevision.current ?? (await save());
      if (!mounted.current) throw new Error("Draft editor closed before commit");
      commitRevision.current = confirmed;
      await commitIncomingDraftRow(documentId, rowId, confirmed);
      invalidate();
      void queryClient.invalidateQueries({ queryKey: incomingKeys.doc(documentId) });
      void queryClient.invalidateQueries({ queryKey: ["incoming", "list"] });
      if (mounted.current) setCommitUncertain(false);
    } catch (error) {
      const status = isAxiosError(error) ? error.response?.status : undefined;
      if (status === 404 || status === 409 || status === 422) {
        const wasCommit = commitRevision.current !== null;
        commitRevision.current = null;
        conflicted.current = status === 404 || status === 409;
        if (mounted.current) {
          setCommitUncertain(false);
          if (conflicted.current) setPhase("conflict");
          else if (wasCommit) setPhase("saved");
        }
      } else if (commitRevision.current !== null && mounted.current) {
        setCommitUncertain(true);
      }
      throw error;
    } finally {
      if (mounted.current) setCommitPending(false);
    }
  }, [documentId, invalidate, queryClient, rowId, save]);

  const reload = useCallback(async () => {
    const rows = await listIncomingDraftRows(documentId);
    const current = rows.find((candidate) => candidate.id === rowId);
    if (!current) throw new Error("Draft row is no longer pending");
    latest.current = draftFields(current);
    saved.current = JSON.stringify(latest.current);
    revision.current = current.revision;
    pendingSave.current = null;
    conflicted.current = false;
    commitRevision.current = null;
    if (mounted.current) {
      setPhase("saved");
      setCommitUncertain(false);
    }
    invalidate();
    return current;
  }, [documentId, invalidate, rowId]);

  return {
    phase,
    update,
    save,
    commit,
    reload,
    commitPending,
    commitUncertain,
    unsaved: enabled && (commitPending || commitUncertain || !["idle", "saved"].includes(phase)),
  };
}
