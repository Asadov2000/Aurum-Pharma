import { getAuthEpoch, subscribeLogoutState } from "@/features/auth/logoutState";
import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";

import { formatBillingDateTime, formatBillingMoney } from "./format";
import { type CashReceipt } from "./cashTypes";

/** Builds an isolated print document using text nodes, never customer HTML. */
export async function printCashReceipt(receipt: CashReceipt): Promise<void> {
  const { user, accessToken } = useAuthStore.getState();
  if (!user || !accessToken) return;
  const generation = getAuthSessionGeneration();
  const epoch = getAuthEpoch();
  const isCurrentAuthority = () => {
    const current = useAuthStore.getState();
    return (
      !!current.accessToken &&
      current.user?.id === user.id &&
      current.user?.active_tenant_id === user.active_tenant_id &&
      getAuthSessionGeneration() === generation &&
      getAuthEpoch() === epoch
    );
  };
  const frame = document.createElement("iframe");
  frame.title = "Печать подтверждения приёма наличных";
  frame.setAttribute("aria-hidden", "true");
  frame.setAttribute("sandbox", "allow-same-origin allow-modals");
  frame.style.cssText = "position:fixed;width:0;height:0;border:0;left:-9999px";
  document.body.appendChild(frame);
  let disposed = false;
  let timeout: number | undefined;
  let unsubscribeAuth: (() => void) | undefined;
  let unsubscribeLogout: (() => void) | undefined;
  let printWindow: Window | null = null;
  let cancelWaiting: (() => void) | undefined;
  const cancelled = new Promise<void>((resolve) => {
    cancelWaiting = resolve;
  });
  const cleanup = () => {
    if (disposed) return;
    disposed = true;
    if (timeout !== undefined) window.clearTimeout(timeout);
    printWindow?.removeEventListener("afterprint", cleanup);
    unsubscribeAuth?.();
    unsubscribeLogout?.();
    frame.remove();
    cancelWaiting?.();
  };
  const checkAuthority = () => {
    if (!isCurrentAuthority()) cleanup();
  };
  try {
    const doc = frame.contentDocument;
    printWindow = frame.contentWindow;
    if (!doc || !printWindow) throw new Error("Print frame unavailable");
    printWindow.addEventListener("afterprint", cleanup, { once: true });
    timeout = window.setTimeout(cleanup, 120_000);
    unsubscribeAuth = useAuthStore.subscribe(checkAuthority);
    unsubscribeLogout = subscribeLogoutState(checkAuthority);
    checkAuthority();
    if (disposed) return;
    doc.title = `${receipt.title} ${receipt.document_number}`;
    doc.documentElement.lang = "ru";
    const style = doc.createElement("style");
    style.textContent =
      "@page{size:A4;margin:20mm}body{font:15px/1.5 Arial,sans-serif;color:#12333d}h1{font-size:25px}h2{font-size:19px}dl{display:grid;grid-template-columns:180px 1fr;gap:12px}dt{color:#52646a}dd{margin:0;overflow-wrap:anywhere}p{overflow-wrap:anywhere}.total{font-size:27px;font-weight:bold;border-top:2px solid #00556b;padding-top:16px}footer{margin-top:35px;color:#52646a;font-size:12px}";
    doc.head.appendChild(style);
    const add = (tag: "h1" | "h2" | "p" | "footer", value: string) => {
      const node = doc.createElement(tag);
      node.textContent = value;
      doc.body.appendChild(node);
      return node;
    };
    add("h1", "Aurum Pharma");
    add("h2", receipt.title);
    add("p", `Документ № ${receipt.document_number}`);
    const details = doc.createElement("dl");
    const rows: readonly (readonly [string, string])[] = [
      ["Клиент", receipt.tenant_name],
      ["Назначение", receipt.purpose],
      ["Получено наличными", formatBillingDateTime(receipt.paid_at)],
      ["Подтверждено", formatBillingDateTime(receipt.confirmed_at)],
      ["Зачтено в счета", formatBillingMoney(receipt.allocated_amount, receipt.currency)],
      ["Аванс", formatBillingMoney(receipt.credit_amount, receipt.currency)],
    ];
    for (const [label, value] of rows) {
      const term = doc.createElement("dt");
      term.textContent = label;
      const definition = doc.createElement("dd");
      definition.textContent = value;
      details.append(term, definition);
    }
    doc.body.appendChild(details);
    add("p", formatBillingMoney(receipt.amount, receipt.currency)).className = "total";
    for (const allocation of receipt.allocations) {
      add(
        "p",
        `Счёт ${allocation.invoice_number}: ${formatBillingMoney(allocation.amount, receipt.currency)}`,
      );
    }
    add(
      "footer",
      "Подтверждение для учёта взаиморасчётов с Aurum Pharma. Не является фискальным чеком.",
    );
    if (doc.fonts) await Promise.race([doc.fonts.ready, cancelled]);
    checkAuthority();
    if (disposed || !frame.isConnected) {
      cleanup();
      return;
    }
    printWindow.focus();
    checkAuthority();
    if (disposed) return;
    printWindow.print();
  } catch (error) {
    cleanup();
    throw error;
  }
}
