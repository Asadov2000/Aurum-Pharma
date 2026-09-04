import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { usePosRegisterLock } from "@/features/pos/usePosRegisterLock";

const REGISTER_ID = "register-1";

interface PendingRequest {
  callback: (lock: Lock) => Promise<void> | void;
  reject: (reason?: unknown) => void;
  resolve: () => void;
  signal?: AbortSignal;
}

function createLockManager(): LockManager {
  let active = false;
  const queue: PendingRequest[] = [];

  const runNext = (): void => {
    if (active) return;
    const request = queue.shift();
    if (!request) return;
    if (request.signal?.aborted) {
      request.reject(new DOMException("Aborted", "AbortError"));
      runNext();
      return;
    }
    active = true;
    void Promise.resolve(
      request.callback({ name: `aurum:pos:register:${REGISTER_ID}`, mode: "exclusive" } as Lock),
    ).then(
      () => {
        active = false;
        request.resolve();
        runNext();
      },
      (error: unknown) => {
        active = false;
        request.reject(error);
        runNext();
      },
    );
  };

  return {
    query: vi.fn(),
    request: vi.fn(
      (
        _name: string,
        options: LockOptions,
        callback: (lock: Lock) => Promise<void> | void,
      ): Promise<void> =>
        new Promise<void>((resolve, reject) => {
          const request: PendingRequest = { callback, reject, resolve, signal: options.signal };
          const onAbort = (): void => {
            const index = queue.indexOf(request);
            if (index >= 0) {
              queue.splice(index, 1);
              reject(new DOMException("Aborted", "AbortError"));
            }
          };
          options.signal?.addEventListener("abort", onAbort, { once: true });
          queue.push(request);
          runNext();
        }),
    ),
  } as unknown as LockManager;
}

function setLockManager(lockManager: LockManager | undefined): void {
  Object.defineProperty(window.navigator, "locks", {
    configurable: true,
    value: lockManager,
  });
}

describe("usePosRegisterLock", () => {
  afterEach(() => {
    setLockManager(undefined);
  });

  it("gives one tab exclusive ownership and transfers it after unmount", async () => {
    setLockManager(createLockManager());
    const first = renderHook(() => usePosRegisterLock(REGISTER_ID));
    const second = renderHook(() => usePosRegisterLock(REGISTER_ID));

    await waitFor(() => expect(first.result.current.isOwner).toBe(true));
    await waitFor(() => expect(second.result.current.status).toBe("blocked"));

    first.unmount();

    await waitFor(() => expect(second.result.current.isOwner).toBe(true));
    second.unmount();
  });

  it("fails closed when Web Locks are unavailable", async () => {
    setLockManager(undefined);

    const hook = renderHook(() => usePosRegisterLock(REGISTER_ID));

    await waitFor(() => expect(hook.result.current.status).toBe("unsupported"));
    expect(hook.result.current.isOwner).toBe(false);
    expect(hook.result.current.message).toMatch(/обновите браузер/i);
    hook.unmount();
  });
});
