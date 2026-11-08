import { useState } from "react";

type IncomingDetailLayout = "classic" | "workspace";
const STORAGE_KEY = "aurum:incoming-detail-layout:v1";

function readLayout(): IncomingDetailLayout {
  try {
    return localStorage.getItem(STORAGE_KEY) === "classic" ? "classic" : "workspace";
  } catch {
    return "workspace";
  }
}

/** Only a presentation preference; no document, medicine or monetary data. */
export function useIncomingDetailLayout(): readonly [
  IncomingDetailLayout,
  (next: IncomingDetailLayout) => void,
] {
  const [layout, setLayout] = useState<IncomingDetailLayout>(readLayout);
  const changeLayout = (next: IncomingDetailLayout) => {
    setLayout(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // The selected view still works for this visit if browser storage is blocked.
    }
  };
  return [layout, changeLayout];
}
