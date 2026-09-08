import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { AxiosError } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type ReactNode } from "react";

const saveRow = vi.fn();
const commitRow = vi.fn();
const listRows = vi.fn();
vi.mock("@/features/incoming/api", () => ({
  saveIncomingDraftRow: (...args: unknown[]) => saveRow(...args),
  commitIncomingDraftRow: (...args: unknown[]) => commitRow(...args),
  listIncomingDraftRows: (...args: unknown[]) => listRows(...args),
}));

import {
  DRAFT_SAVE_DELAY,
  EMPTY_DRAFT_FIELDS,
  useIncomingDraft,
} from "@/features/incoming/useIncomingDraft";
import { type IncomingDraftRow } from "@/features/incoming/types";

const ROW: IncomingDraftRow = {
  id: "740a9424-3693-4535-815a-608cf81a22a5",
  document_id: "doc-1",
  revision: 3,
  fields: { ...EMPTY_DRAFT_FIELDS, qty: "12," },
  catalog_name: "Парацетамол",
  created_at: "2026-09-08T00:00:00Z",
  updated_at: "2026-09-08T00:00:00Z",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function setup(row?: IncomingDraftRow) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return renderHook(() => useIncomingDraft("doc-1", row, true), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}

describe("incoming draft persistence", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    saveRow.mockReset();
    commitRow.mockReset();
    listRows.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("debounces raw input and saves newer edits after the in-flight revision before commit", async () => {
    const first = deferred<IncomingDraftRow>();
    const second = deferred<IncomingDraftRow>();
    saveRow.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    commitRow.mockResolvedValue({ id: "item-1" });
    const { result } = setup();
    act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "12," }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DRAFT_SAVE_DELAY - 1);
    });
    expect(saveRow).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(result.current.phase).toBe("saving");
    act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "13," }));
    let commit!: Promise<void>;
    act(() => {
      commit = result.current.commit();
    });
    expect(commitRow).not.toHaveBeenCalled();
    await act(async () => {
      first.resolve({ ...ROW, revision: 1 });
    });
    expect(saveRow.mock.calls[1]?.[2]).toEqual({
      expected_revision: 1,
      fields: { ...EMPTY_DRAFT_FIELDS, qty: "13," },
    });
    expect(commitRow).not.toHaveBeenCalled();
    await act(async () => {
      second.resolve({ ...ROW, revision: 2 });
      await commit;
    });
    const rowId = saveRow.mock.calls[0]?.[1];
    expect(rowId).toMatch(/^[0-9a-f-]{36}$/);
    expect(saveRow.mock.calls[1]?.[1]).toBe(rowId);
    expect(commitRow).toHaveBeenCalledWith("doc-1", rowId, 2);
  });

  it("retries a lost PUT reply with its old snapshot before writing newer input", async () => {
    saveRow
      .mockRejectedValueOnce(new Error("response lost"))
      .mockResolvedValueOnce({ ...ROW, revision: 1 })
      .mockResolvedValueOnce({ ...ROW, revision: 2 });
    const { result } = setup();
    act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "12," }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DRAFT_SAVE_DELAY);
    });
    expect(result.current.phase).toBe("error");
    expect(result.current.unsaved).toBe(true);
    act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "14," }));
    await act(async () => {
      await result.current.save();
    });
    expect(saveRow.mock.calls[1]).toEqual(saveRow.mock.calls[0]);
    expect(saveRow.mock.calls[2]?.[2]).toEqual({
      expected_revision: 1,
      fields: { ...EMPTY_DRAFT_FIELDS, qty: "14," },
    });
    expect(result.current.phase).toBe("saved");
    expect(result.current.unsaved).toBe(false);
  });

  it("retries an uncertain commit with the same row and revision without another PUT", async () => {
    commitRow
      .mockRejectedValueOnce(new Error("response lost"))
      .mockResolvedValueOnce({ id: "item-1" });
    const { result } = setup(ROW);
    await act(async () => {
      await result.current.commit().catch(() => undefined);
    });
    expect(result.current.commitUncertain).toBe(true);
    expect(result.current.unsaved).toBe(true);
    await act(async () => {
      await result.current.commit();
    });
    expect(commitRow.mock.calls).toEqual([
      ["doc-1", ROW.id, 3],
      ["doc-1", ROW.id, 3],
    ]);
    expect(saveRow).not.toHaveBeenCalled();
    expect(result.current.commitUncertain).toBe(false);
  });

  it.each([401, 403, 429])(
    "preserves a lost PUT snapshot through a temporary %s response",
    async (status) => {
      saveRow
        .mockRejectedValueOnce(new Error("successful PUT reply lost"))
        .mockRejectedValueOnce(
          Object.assign(new AxiosError("request blocked"), { response: { status } }),
        )
        .mockResolvedValueOnce({ ...ROW, revision: 1 })
        .mockResolvedValueOnce({ ...ROW, revision: 2 });
      const { result } = setup();
      act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "12," }));
      await act(async () => {
        await result.current.save().catch(() => undefined);
      });
      await act(async () => {
        await result.current.save().catch(() => undefined);
      });
      act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "13," }));
      await act(async () => {
        await result.current.save();
      });
      expect(saveRow.mock.calls[1]).toEqual(saveRow.mock.calls[0]);
      expect(saveRow.mock.calls[2]).toEqual(saveRow.mock.calls[0]);
      expect(saveRow.mock.calls[3]?.[2]).toEqual({
        expected_revision: 1,
        fields: { ...EMPTY_DRAFT_FIELDS, qty: "13," },
      });
      expect(result.current.phase).toBe("saved");
    },
  );

  it.each([401, 403, 429])(
    "preserves uncertain commit identity and input protection after %s",
    async (status) => {
      commitRow
        .mockRejectedValueOnce(new Error("successful commit reply lost"))
        .mockRejectedValueOnce(
          Object.assign(new AxiosError("request blocked"), { response: { status } }),
        )
        .mockResolvedValueOnce({ id: "item-1" });
      const { result } = setup(ROW);
      await act(async () => {
        await result.current.commit().catch(() => undefined);
      });
      await act(async () => {
        await result.current.commit().catch(() => undefined);
      });
      expect(result.current.commitUncertain).toBe(true);
      expect(result.current.unsaved).toBe(true);
      await act(async () => {
        await result.current.commit();
      });
      expect(commitRow.mock.calls).toEqual(Array.from({ length: 3 }, () => ["doc-1", ROW.id, 3]));
      expect(saveRow).not.toHaveBeenCalled();
      expect(result.current.commitUncertain).toBe(false);
    },
  );

  it("blocks saving and committing after a conflict until explicit reload", async () => {
    saveRow.mockRejectedValue(
      Object.assign(new AxiosError("conflict"), { response: { status: 409 } }),
    );
    listRows.mockResolvedValue([{ ...ROW, revision: 4, fields: { ...ROW.fields, qty: "20" } }]);
    const { result } = setup(ROW);
    act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "15," }));
    await act(async () => {
      await result.current.save().catch(() => undefined);
    });
    expect(result.current.phase).toBe("conflict");
    act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "16," }));
    await act(async () => {
      await result.current.commit().catch(() => undefined);
    });
    expect(saveRow).toHaveBeenCalledTimes(1);
    expect(commitRow).not.toHaveBeenCalled();
    await act(async () => {
      expect((await result.current.reload()).fields.qty).toBe("20");
    });
    expect(result.current.phase).toBe("saved");
  });

  it("does not send queued edits after unmount but lets the already sent mutation finish", async () => {
    const first = deferred<IncomingDraftRow>();
    saveRow.mockReturnValue(first.promise);
    const { result, unmount } = setup();
    act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "12," }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DRAFT_SAVE_DELAY);
    });
    act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "13," }));
    unmount();
    await act(async () => {
      first.resolve({ ...ROW, revision: 1 });
      await vi.advanceTimersByTimeAsync(DRAFT_SAVE_DELAY);
    });
    expect(saveRow).toHaveBeenCalledTimes(1);
  });

  it("does not start a commit after the editor closes while waiting for PUT", async () => {
    const pending = deferred<IncomingDraftRow>();
    saveRow.mockReturnValue(pending.promise);
    const { result, unmount } = setup();
    act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "12" }));
    let commit!: Promise<void>;
    act(() => {
      commit = result.current.commit().catch(() => undefined);
    });
    expect(saveRow).toHaveBeenCalledTimes(1);
    unmount();
    await act(async () => {
      pending.resolve({ ...ROW, revision: 1 });
      await commit;
    });
    expect(commitRow).not.toHaveBeenCalled();
  });

  it("allows correcting a definitively rejected PUT instead of retrying invalid fields forever", async () => {
    saveRow
      .mockRejectedValueOnce(
        Object.assign(new AxiosError("invalid"), { response: { status: 422 } }),
      )
      .mockResolvedValueOnce({ ...ROW, revision: 1 });
    const { result } = setup();
    act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "x".repeat(65) }));
    await act(async () => {
      await result.current.save().catch(() => undefined);
    });
    act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "12" }));
    await act(async () => {
      await result.current.save();
    });
    expect(saveRow.mock.calls[1]?.[2]).toEqual({
      expected_revision: 0,
      fields: { ...EMPTY_DRAFT_FIELDS, qty: "12" },
    });
    expect(result.current.phase).toBe("saved");
  });

  it("unlocks a rejected commit for correction rather than treating 422 as an uncertain success", async () => {
    commitRow.mockRejectedValue(
      Object.assign(new AxiosError("invalid"), { response: { status: 422 } }),
    );
    saveRow.mockResolvedValue({ ...ROW, revision: 4 });
    const { result } = setup(ROW);
    await act(async () => {
      await result.current.commit().catch(() => undefined);
    });
    expect(result.current.commitUncertain).toBe(false);
    act(() => result.current.update({ ...EMPTY_DRAFT_FIELDS, qty: "12" }));
    await act(async () => {
      await result.current.save();
    });
    expect(saveRow.mock.calls[0]?.[2].expected_revision).toBe(3);
  });

  it.each([409, 422])(
    "resolves an uncertain commit after a definitive %s response",
    async (status) => {
      commitRow
        .mockRejectedValueOnce(new Error("lost response"))
        .mockRejectedValueOnce(Object.assign(new AxiosError("rejected"), { response: { status } }));
      const { result } = setup(ROW);
      await act(async () => {
        await result.current.commit().catch(() => undefined);
      });
      expect(result.current.commitUncertain).toBe(true);
      await act(async () => {
        await result.current.commit().catch(() => undefined);
      });
      expect(result.current.commitUncertain).toBe(false);
      expect(result.current.phase).toBe(status === 409 ? "conflict" : "saved");
      expect(saveRow).not.toHaveBeenCalled();
    },
  );
});
