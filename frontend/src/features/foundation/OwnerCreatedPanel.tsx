import { useState } from "react";

import { Button } from "@/components/ui";
import { requestLoginCode } from "@/features/auth/api";

import { describeApiError } from "./errors";

/** Success panel after a pharmacy + owner are created: confirms what was made
 *  and offers a login-code helper so the brand-new owner can sign in (they have
 *  no password — login is by code). dev_code is shown only in dev. */
export function OwnerCreatedPanel({
  info,
  onClose,
}: {
  info: { pharmacy: string; ownerEmail: string };
  onClose: () => void;
}): JSX.Element {
  const [devCode, setDevCode] = useState<string | null | undefined>(undefined);
  const [codeError, setCodeError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const getCode = async () => {
    setCodeError(null);
    setLoading(true);
    try {
      const res = await requestLoginCode({ email: info.ownerEmail });
      setDevCode(res.dev_code);
    } catch (err) {
      setCodeError(describeApiError(err, "Не удалось запросить код входа"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-success/40 bg-success/[0.06] p-4">
        <p className="font-medium text-foreground">Аптека и владелец созданы</p>
        <p className="mt-1 text-sm text-foreground-secondary">
          Аптека: <span className="font-medium text-foreground">{info.pharmacy}</span>
        </p>
        <p className="text-sm text-foreground-secondary">
          Владелец: <span className="font-medium text-foreground">{info.ownerEmail}</span>
        </p>
      </div>

      <div className="space-y-2">
        <p className="text-sm text-foreground-muted">
          Владелец входит по коду — пароля у него нет. Получите код для входа:
        </p>
        <Button type="button" variant="secondary" onClick={() => void getCode()} isLoading={loading}>
          Получить код входа
        </Button>
        {codeError && <p className="text-sm text-danger">{codeError}</p>}
        {devCode !== undefined &&
          (devCode ? (
            <p className="text-sm text-foreground-secondary">
              Код входа: <span className="font-mono text-lg text-foreground">{devCode}</span>{" "}
              <span className="text-xs text-foreground-muted">(код только для dev-режима)</span>
            </p>
          ) : (
            <p className="text-sm text-foreground-muted">
              Код отправлен по обычному каналу (вне dev-режима код в ответе не показывается).
            </p>
          ))}
      </div>

      <div className="flex justify-end">
        <Button type="button" onClick={onClose}>
          Готово
        </Button>
      </div>
    </div>
  );
}
