export type PwaInstallOutcome = "accepted" | "dismissed";

export interface BeforeInstallPromptEvent extends Event {
  readonly platforms: readonly string[];
  readonly userChoice: Promise<{
    readonly outcome: PwaInstallOutcome;
    readonly platform: string;
  }>;
  prompt(): Promise<void>;
}

declare global {
  interface WindowEventMap {
    beforeinstallprompt: BeforeInstallPromptEvent;
    appinstalled: Event;
  }
}

export function registerPwaServiceWorker(): void {
  if (import.meta.env.DEV) {
    return;
  }

  if (!("serviceWorker" in navigator)) {
    return;
  }

  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((error: unknown) => {
      console.warn("pwa_service_worker_registration_failed", error);
    });
  });
}
