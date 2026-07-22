import { createPortal } from "react-dom";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { cn } from "@/lib/utils";

import { Button } from "./Button";

export interface ActionMenuItem {
  label: string;
  onSelect: () => void;
  tone?: "default" | "danger";
}

interface ActionMenuProps {
  label: string;
  items: ActionMenuItem[];
  isLoading?: boolean;
}

const MENU_WIDTH = 224;
const MENU_ITEM_HEIGHT = 32;
const VIEWPORT_GUTTER = 8;

export function ActionMenu({ label, items, isLoading = false }: ActionMenuProps): JSX.Element {
  const menuId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0 });

  const closeMenu = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) {
      requestAnimationFrame(() => triggerRef.current?.focus());
    }
  };

  const openMenu = () => {
    const trigger = triggerRef.current;
    if (!trigger) return;

    const rect = trigger.getBoundingClientRect();
    const estimatedHeight = items.length * MENU_ITEM_HEIGHT + VIEWPORT_GUTTER;
    const opensUpward = rect.bottom + estimatedHeight > window.innerHeight - VIEWPORT_GUTTER;
    setPosition({
      left: Math.max(
        VIEWPORT_GUTTER,
        Math.min(rect.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - VIEWPORT_GUTTER),
      ),
      top: opensUpward
        ? Math.max(VIEWPORT_GUTTER, rect.top - estimatedHeight - 4)
        : rect.bottom + 4,
    });
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return undefined;

    requestAnimationFrame(() => {
      menuRef.current
        ?.querySelector<HTMLButtonElement>('[role="menuitem"]')
        ?.focus({ preventScroll: true });
    });

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      setOpen(false);
    };
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeMenu(true);
    };
    const handleViewportChange = () => setOpen(false);

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", handleViewportChange);
    window.addEventListener("scroll", handleViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("scroll", handleViewportChange, true);
    };
  }, [open]);

  const moveMenuFocus = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!menuRef.current) return;
    const menuItems = Array.from(
      menuRef.current.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'),
    );
    if (menuItems.length === 0) return;

    const currentIndex = menuItems.indexOf(document.activeElement as HTMLButtonElement);
    let nextIndex: number | null = null;
    if (event.key === "ArrowDown") nextIndex = (currentIndex + 1) % menuItems.length;
    if (event.key === "ArrowUp") {
      nextIndex = (currentIndex - 1 + menuItems.length) % menuItems.length;
    }
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = menuItems.length - 1;
    if (nextIndex === null) return;

    event.preventDefault();
    menuItems[nextIndex]?.focus();
  };

  return (
    <>
      <Button
        ref={triggerRef}
        variant="ghost"
        size="sm"
        className="w-8 px-0 text-xl leading-none"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title={label}
        isLoading={isLoading}
        onClick={() => (open ? closeMenu() : openMenu())}
      >
        <span aria-hidden="true">⋮</span>
      </Button>

      {open &&
        createPortal(
          <div
            ref={menuRef}
            id={menuId}
            role="menu"
            aria-label={label}
            aria-orientation="vertical"
            className="fixed z-[100] w-56 rounded-md border border-border bg-surface p-1 shadow-lg"
            style={position}
            onKeyDown={moveMenuFocus}
          >
            {items.map((item) => (
              <Button
                key={item.label}
                role="menuitem"
                tabIndex={-1}
                variant="ghost"
                size="sm"
                className={cn(
                  "w-full justify-start px-3 font-medium",
                  item.tone === "danger" && "text-danger hover:bg-danger/10",
                )}
                onClick={() => {
                  setOpen(false);
                  item.onSelect();
                }}
              >
                {item.label}
              </Button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}
