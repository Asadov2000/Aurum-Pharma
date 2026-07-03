export type PwaInstallOutcome = "accepted" | "dismissed";
export const PWA_UPDATE_READY_EVENT = "aurum-pwa-update-ready";

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
    "aurum-pwa-update-ready": Event;
    beforeinstallprompt: BeforeInstallPromptEvent;
    appinstalled: Event;
  }
}

interface RegisterPwaServiceWorkerOptions {
  readonly isDev?: boolean;
  readonly onUpdateReady?: () => void;
  readonly serviceWorker?: ServiceWorkerContainer;
}

export function registerPwaServiceWorker(
  options: RegisterPwaServiceWorkerOptions = {},
): void {
  if (options.isDev ?? import.meta.env.DEV) {
    return;
  }

  const serviceWorker = resolveServiceWorkerContainer(options.serviceWorker);
  if (!serviceWorker) {
    return;
  }

  const register = () => {
    registerServiceWorker(serviceWorker, options.onUpdateReady).catch((error: unknown) => {
      console.warn("pwa_service_worker_registration_failed", error);
    });
  };

  if (document.readyState === "complete") {
    register();
    return;
  }

  window.addEventListener("load", register, { once: true });
}

async function registerServiceWorker(
  serviceWorker: ServiceWorkerContainer,
  onUpdateReady?: () => void,
): Promise<void> {
  const hadController = Boolean(serviceWorker.controller);
  let notified = false;
  const notifyUpdateReady = () => {
    if (notified || !hadController) {
      return;
    }

    notified = true;
    onUpdateReady?.();
    window.dispatchEvent(new Event(PWA_UPDATE_READY_EVENT));
  };

  const registration = await serviceWorker.register("/sw.js");

  if (registration.waiting) {
    notifyUpdateReady();
  }

  registration.addEventListener("updatefound", () => {
    const installingWorker = registration.installing;
    if (!installingWorker) {
      return;
    }

    installingWorker.addEventListener("statechange", () => {
      if (installingWorker.state === "installed") {
        notifyUpdateReady();
      }
    });
  });

  serviceWorker.addEventListener("controllerchange", notifyUpdateReady);
}

function resolveServiceWorkerContainer(
  serviceWorker?: ServiceWorkerContainer,
): ServiceWorkerContainer | null {
  if (serviceWorker) {
    return serviceWorker;
  }

  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    return null;
  }

  return navigator.serviceWorker;
}
