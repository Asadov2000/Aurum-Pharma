import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui";

import { preparePosRecovery } from "./draftRecovery";
import { capturePosSessionGuard, type PosStorageScope } from "./posStorageScope";

export function PosRecoveryGate({
  registerId,
  scope,
  children,
}: {
  registerId: string;
  scope: PosStorageScope;
  children: ReactNode;
}): JSX.Element {
  const [status, setStatus] = useState<"checking" | "ready" | "blocked">("checking");
  const [attempt, setAttempt] = useState(0);
  const [sessionIsActive] = useState(() => capturePosSessionGuard(scope));

  useEffect(() => {
    let disposed = false;
    const isActive = () => !disposed && sessionIsActive();
    void preparePosRecovery(registerId, scope, isActive).then((ready) => {
      if (isActive()) setStatus(ready ? "ready" : "blocked");
    });
    return () => {
      disposed = true;
    };
  }, [attempt, registerId, scope, sessionIsActive]);

  if (status === "ready") return <>{children}</>;
  return (
    <div role="status" className="rounded-lg border border-border bg-surface px-4 py-3 text-sm">
      <p className="font-semibold">
        {status === "checking"
          ? "Проверяем сохранённую работу кассы…"
          : "Нужна проверка прошлой операции"}
      </p>
      {status === "blocked" ? (
        <>
          <p className="mt-1">
            Проверьте связь. Если операция принадлежит другому сотруднику, попросите его войти и
            завершить сверку. Сохранённые данные остаются на этом компьютере.
          </p>
          <Button
            className="mt-3"
            variant="secondary"
            onClick={() => {
              setStatus("checking");
              setAttempt((value) => value + 1);
            }}
          >
            Проверить снова
          </Button>
        </>
      ) : null}
    </div>
  );
}
