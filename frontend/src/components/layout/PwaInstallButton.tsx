import { useEffect, useState } from "react";

import { Button } from "@/components/ui";
import { type BeforeInstallPromptEvent } from "@/lib/pwa";
import { detectRuntimeSurface, type RuntimeSurface } from "@/lib/runtime";

export function PwaInstallButton({
  surface = detectRuntimeSurface(),
}: {
  surface?: RuntimeSurface;
}): JSX.Element | null {
  const [installPrompt, setInstallPrompt] =
    useState<BeforeInstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(surface !== "browser");

  useEffect(() => {
    const alreadyInstalled = surface !== "browser";
    setInstalled(alreadyInstalled);
    if (alreadyInstalled) {
      setInstallPrompt(null);
      return undefined;
    }

    const onBeforeInstallPrompt = (event: BeforeInstallPromptEvent) => {
      event.preventDefault();
      setInstallPrompt(event);
      setInstalled(false);
    };
    const onAppInstalled = () => {
      setInstallPrompt(null);
      setInstalled(true);
    };

    window.addEventListener("beforeinstallprompt", onBeforeInstallPrompt);
    window.addEventListener("appinstalled", onAppInstalled);

    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstallPrompt);
      window.removeEventListener("appinstalled", onAppInstalled);
    };
  }, [surface]);

  if (installed || installPrompt === null) {
    return null;
  }

  const onInstall = () => {
    const promptEvent = installPrompt;
    setInstallPrompt(null);

    void promptEvent
      .prompt()
      .then(() => promptEvent.userChoice)
      .then((choice) => {
        if (choice.outcome === "accepted") {
          setInstalled(true);
        }
      })
      .catch((error: unknown) => {
        console.warn("pwa_install_prompt_failed", error);
      });
  };

  return (
    <Button
      aria-label="Установить приложение"
      className="h-8 w-8 px-0 md:w-auto md:px-3"
      data-testid="pwa-install-button"
      onClick={onInstall}
      size="sm"
      title="Установить приложение"
      variant="secondary"
    >
      <InstallIcon />
      <span className="sr-only md:not-sr-only">Установить</span>
    </Button>
  );
}

function InstallIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height="16"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
      width="16"
    >
      <path d="M12 3v12" />
      <path d="m7 10 5 5 5-5" />
      <path d="M5 21h14" />
    </svg>
  );
}
