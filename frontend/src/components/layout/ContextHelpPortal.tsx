import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { ContextHelpTrigger } from "./ContextHelpTrigger";

export function ContextHelpPortal({ pathname }: { pathname: string }): JSX.Element | null {
  const [slot, setSlot] = useState<HTMLElement | null>(null);

  useEffect(() => {
    setSlot(document.getElementById("app-context-help-slot"));
  }, []);

  return slot ? createPortal(<ContextHelpTrigger pathname={pathname} />, slot) : null;
}
