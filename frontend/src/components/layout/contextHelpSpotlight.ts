let clearCurrent: (() => void) | null = null;

export function clearHelpSpotlight(): void {
  clearCurrent?.();
}

export function showHelpSpotlight(target: HTMLElement): void {
  clearHelpSpotlight();
  target.classList.add("aurum-help-target");
  const observer = new MutationObserver(() => {
    if (!target.isConnected) clear();
  });
  const clear = () => {
    target.classList.remove("aurum-help-target");
    document.removeEventListener("pointerdown", clear);
    document.removeEventListener("keydown", clear);
    observer.disconnect();
    if (clearCurrent === clear) clearCurrent = null;
  };
  clearCurrent = clear;
  target.scrollIntoView?.({ behavior: "smooth", block: "center" });
  if (target.matches("input, textarea, select")) target.focus({ preventScroll: true });
  document.addEventListener("pointerdown", clear);
  document.addEventListener("keydown", clear);
  observer.observe(document.body, { childList: true, subtree: true });
}
