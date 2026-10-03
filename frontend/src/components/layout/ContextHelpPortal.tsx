import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { ContextHelpTrigger } from "./ContextHelpTrigger";
import { type HelpLocation } from "./ContextHelp";

export function ContextHelpPortal({
  pathname,
  operationKind,
  inventorySection,
  readOnly,
}: HelpLocation): JSX.Element | null {
  const [slot, setSlot] = useState<HTMLElement | null>(null);

  useEffect(() => {
    setSlot(document.getElementById("app-context-help-slot"));
  }, []);

  return slot
    ? createPortal(
        <ContextHelpTrigger
          pathname={pathname}
          operationKind={operationKind}
          inventorySection={inventorySection}
          readOnly={readOnly}
        />,
        slot,
      )
    : null;
}
