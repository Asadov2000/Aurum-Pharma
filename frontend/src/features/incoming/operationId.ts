export function createIncomingOperationId(): string {
  if (typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  throw new Error("Безопасный генератор кода операции недоступен");
}
