import { useEffect, useRef } from "react";

import { Button } from "@/components/ui/Button";

import { Sidebar, type NavItem } from "./Sidebar";

interface MobileNavigationPanelProps {
  items: NavItem[];
  favoriteRoutes: readonly string[];
  onClose: () => void;
  onOpenSettings: () => void;
}

export default function MobileNavigationPanel({
  items,
  favoriteRoutes,
  onClose,
  onOpenSettings,
}: MobileNavigationPanelProps): JSX.Element {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const focusableSelector = [
      "a[href]",
      "button:not([disabled])",
      "textarea:not([disabled])",
      "input:not([disabled])",
      "select:not([disabled])",
      '[tabindex]:not([tabindex="-1"])',
    ].join(",");

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;

      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (element) => element.offsetParent !== null && element.tabIndex >= 0,
      );
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    const focusFrame = window.requestAnimationFrame(() => closeButtonRef.current?.focus());
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  return (
    <div ref={panelRef} className="h-full">
      <Sidebar
        items={items}
        mode="drawer"
        favoriteRoutes={favoriteRoutes}
        onNavigate={onClose}
        onOpenSettings={onOpenSettings}
        closeButton={
          <Button
            ref={closeButtonRef}
            variant="ghost"
            size="sm"
            className="h-9 w-9 px-0"
            aria-label="Закрыть меню"
            onClick={onClose}
          >
            <CloseIcon />
          </Button>
        }
      />
    </div>
  );
}

function CloseIcon(): JSX.Element {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}
