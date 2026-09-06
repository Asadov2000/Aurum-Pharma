import { lazy, Suspense, useEffect, useRef } from "react";
import { createPortal } from "react-dom";

import { MfaSetupPrompt } from "./MfaSetupPrompt";
import { useMfaStepUpRequested } from "./stepUpCoordinator";

const MfaStepUpDialog = lazy(async () => {
  const module = await import("./MfaStepUpDialog");
  return { default: module.MfaStepUpDialog };
});

export default function AccountSecuritySurface({
  showPrompt,
}: {
  showPrompt: boolean;
}): JSX.Element {
  const stepUpRequested = useMfaStepUpRequested();
  return (
    <>
      {showPrompt ? <MfaSetupPrompt /> : null}
      {stepUpRequested
        ? createPortal(
            <Suspense fallback={<MfaStepUpLoading />}>
              <MfaStepUpDialog />
            </Suspense>,
            document.body,
          )
        : null}
    </>
  );
}

function MfaStepUpLoading(): JSX.Element {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label="Загрузка подтверждения действия"
      aria-busy="true"
      tabIndex={-1}
      className="fixed inset-0 z-modal flex items-center justify-center bg-overlay p-4 outline-none"
    >
      <div className="rounded-lg border border-border bg-surface-raised px-5 py-4 text-sm text-foreground-muted shadow-xl">
        Загрузка защиты…
      </div>
    </div>
  );
}
