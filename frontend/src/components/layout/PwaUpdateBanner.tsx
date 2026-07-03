import { useEffect, useState } from "react";

import { Button } from "@/components/ui";
import { PWA_UPDATE_READY_EVENT } from "@/lib/pwa";

interface PwaUpdateBannerProps {
  readonly reload?: () => void;
}

export function PwaUpdateBanner({
  reload = () => window.location.reload(),
}: PwaUpdateBannerProps): JSX.Element | null {
  const [isUpdateReady, setIsUpdateReady] = useState(false);

  useEffect(() => {
    const showUpdateReady = () => setIsUpdateReady(true);
    window.addEventListener(PWA_UPDATE_READY_EVENT, showUpdateReady);

    return () => {
      window.removeEventListener(PWA_UPDATE_READY_EVENT, showUpdateReady);
    };
  }, []);

  if (!isUpdateReady) {
    return null;
  }

  return (
    <div
      aria-live="polite"
      className="flex flex-col gap-2 border-b border-primary/25 bg-primary/10 px-4 py-2 text-sm font-semibold text-primary sm:flex-row sm:items-center sm:justify-between sm:px-6"
      data-testid="pwa-update-banner"
      role="status"
    >
      <span>Доступно обновление приложения. Обновите экран, чтобы получить свежую версию.</span>
      <Button
        className="h-8 shrink-0 px-3"
        data-testid="pwa-update-reload-button"
        onClick={reload}
        size="sm"
        type="button"
        variant="secondary"
      >
        Обновить
      </Button>
    </div>
  );
}
