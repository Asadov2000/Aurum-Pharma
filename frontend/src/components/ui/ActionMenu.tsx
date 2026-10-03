import { createPortal } from "react-dom";
import {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ForwardedRef,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

import { Button } from "./Button";

export interface ActionMenuItem {
  id?: string;
  label: string;
  onSelect: () => void;
  tone?: "default" | "danger";
  icon?: ReactNode;
  group?: string;
}

interface ActionMenuProps {
  label: string;
  items: ActionMenuItem[];
  isLoading?: boolean;
  disabled?: boolean;
  contextTarget?: string;
  contextEnabled?: boolean;
  contextTitle?: string;
  contextDetail?: string;
  contextIcon?: ReactNode;
  resetKey?: string;
  onConfigure?: () => void;
}

const MENU_WIDTH = 224;
const RECORD_MENU_WIDTH = 302;
// Conservative across compact, comfortable and touch density modes.
const MENU_ITEM_HEIGHT = 48;
const VIEWPORT_GUTTER = 8;

export const ActionMenu = /* @__PURE__ */ forwardRef<HTMLButtonElement, ActionMenuProps>(
  function ActionMenu(
    {
      label,
      items,
      isLoading = false,
      disabled = false,
      contextTarget,
      contextEnabled = false,
      contextTitle,
      contextDetail,
      contextIcon,
      resetKey,
      onConfigure,
    },
    forwardedRef,
  ): JSX.Element {
    const menuId = useId();
    const triggerRef = useRef<HTMLButtonElement | null>(null);
    const menuRef = useRef<HTMLDivElement>(null);
    const sourceRef = useRef<HTMLElement | null>(null);
    const [open, setOpen] = useState(false);
    const [position, setPosition] = useState({ left: 0, top: 0 });
    const unavailable = disabled || isLoading || (items.length === 0 && !onConfigure);
    const signature = JSON.stringify([
      resetKey,
      label,
      contextEnabled,
      items.map((item) => [item.id, item.label, item.tone, item.group]),
    ]);
    useEffect(() => {
      setOpen(false);
    }, [unavailable, signature]);
    const setTriggerRef = useCallback(
      (element: HTMLButtonElement | null) => {
        triggerRef.current = element;
        setForwardedRef(forwardedRef, element);
      },
      [forwardedRef],
    );

    const closeMenu = (restoreFocus = false) => {
      setOpen(false);
      if (restoreFocus) {
        requestAnimationFrame(() =>
          (sourceRef.current ?? triggerRef.current)?.focus({ preventScroll: true }),
        );
      }
    };

    const openMenu = useCallback(
      (source?: HTMLElement, point?: { left: number; top: number }) => {
        if (unavailable) return;
        const trigger = triggerRef.current;
        if (!trigger) return;

        sourceRef.current = source ?? trigger;
        const rect = sourceRef.current.getBoundingClientRect();
        const width = contextTitle ? RECORD_MENU_WIDTH : MENU_WIDTH;
        const estimatedHeight = Math.min(
          window.innerHeight - VIEWPORT_GUTTER * 2,
          items.length * MENU_ITEM_HEIGHT +
            VIEWPORT_GUTTER +
            (contextTitle
              ? 64 +
                items.filter((item, index) => item.group && item.group !== items[index - 1]?.group)
                  .length *
                  28
              : 0) +
            (onConfigure ? 48 : 0),
        );
        const opensUpward = rect.bottom + estimatedHeight > window.innerHeight - VIEWPORT_GUTTER;
        setPosition({
          left: Math.max(
            VIEWPORT_GUTTER,
            Math.min(
              point?.left ?? rect.right - width,
              window.innerWidth - width - VIEWPORT_GUTTER,
            ),
          ),
          top: point
            ? Math.max(
                VIEWPORT_GUTTER,
                Math.min(point.top, window.innerHeight - estimatedHeight - VIEWPORT_GUTTER),
              )
            : opensUpward
              ? Math.max(VIEWPORT_GUTTER, rect.top - estimatedHeight - 4)
              : rect.bottom + 4,
        });
        document.dispatchEvent(new CustomEvent("aurum:action-menu-open", { detail: menuId }));
        setOpen(true);
      },
      [unavailable, items, contextTitle, onConfigure, menuId],
    );

    useEffect(() => {
      const region = contextTarget ? triggerRef.current?.closest<HTMLElement>(contextTarget) : null;
      if (!region || !contextEnabled || unavailable) return;
      const preserveNative = (target: EventTarget | null) =>
        !(target instanceof Element) ||
        Boolean(
          target.closest(
            'a,input,textarea,select,[contenteditable]:not([contenteditable="false"]),[data-native-context-menu]',
          ),
        ) ||
        Boolean(region.ownerDocument.getSelection()?.toString());
      const context = (event: MouseEvent) => {
        if (event.defaultPrevented || preserveNative(event.target)) return;
        event.preventDefault();
        const rect = region.getBoundingClientRect();
        openMenu(region, {
          left: event.clientX || rect.left + 16,
          top: event.clientY || rect.top + 16,
        });
      };
      const keyboard = (event: globalThis.KeyboardEvent) => {
        if (event.key !== "ContextMenu" && !(event.key === "F10" && event.shiftKey)) return;
        if (event.defaultPrevented || preserveNative(event.target)) return;
        event.preventDefault();
        const source = event.target instanceof HTMLElement ? event.target : region;
        const rect = source.getBoundingClientRect();
        openMenu(source, { left: rect.left + 16, top: rect.top + 16 });
      };
      region.addEventListener("contextmenu", context);
      region.addEventListener("keydown", keyboard);
      return () => {
        region.removeEventListener("contextmenu", context);
        region.removeEventListener("keydown", keyboard);
      };
    }, [contextTarget, contextEnabled, unavailable, openMenu]);

    useEffect(() => {
      if (!open) return undefined;

      let armScrollFrame = 0;
      const focusFrame = requestAnimationFrame(() => {
        const bounds = menuRef.current?.getBoundingClientRect();
        if (bounds)
          setPosition((current) => ({
            left: Math.max(
              VIEWPORT_GUTTER,
              Math.min(current.left, window.innerWidth - bounds.width - VIEWPORT_GUTTER),
            ),
            top: Math.max(
              VIEWPORT_GUTTER,
              Math.min(current.top, window.innerHeight - bounds.height - VIEWPORT_GUTTER),
            ),
          }));
        menuRef.current
          ?.querySelector<HTMLButtonElement>('[role="menuitem"]')
          ?.focus({ preventScroll: true });
        // Browser automation and keyboard focus may finish a pending scroll
        // immediately after the menu opens. Arm scroll dismissal after layout
        // settles so that event cannot close the menu before it is usable.
        armScrollFrame = requestAnimationFrame(() => {
          window.addEventListener("scroll", handleViewportChange, true);
        });
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
      const handleViewportChange = (event: Event) => {
        if (event.target instanceof Node && menuRef.current?.contains(event.target)) return;
        setOpen(false);
      };
      const handleOtherMenu = (event: Event) => {
        if (event instanceof CustomEvent && event.detail !== menuId) setOpen(false);
      };

      document.addEventListener("pointerdown", handlePointerDown);
      document.addEventListener("keydown", handleKeyDown);
      window.addEventListener("resize", handleViewportChange);
      document.addEventListener("aurum:action-menu-open", handleOtherMenu);
      return () => {
        window.cancelAnimationFrame(focusFrame);
        window.cancelAnimationFrame(armScrollFrame);
        document.removeEventListener("pointerdown", handlePointerDown);
        document.removeEventListener("keydown", handleKeyDown);
        window.removeEventListener("resize", handleViewportChange);
        window.removeEventListener("scroll", handleViewportChange, true);
        document.removeEventListener("aurum:action-menu-open", handleOtherMenu);
      };
    }, [open, menuId]);

    const moveMenuFocus = (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Tab") {
        event.preventDefault();
        closeMenu(true);
        return;
      }
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
          ref={setTriggerRef}
          variant="ghost"
          size="sm"
          className="h-[var(--control-height-sm)] w-[var(--control-height-sm)] px-0 text-lg leading-none"
          aria-label={label}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={open ? menuId : undefined}
          title={label}
          isLoading={isLoading}
          disabled={unavailable}
          onClick={() => (open ? closeMenu() : openMenu())}
        >
          <span aria-hidden="true">{contextTitle ? "⋯" : "⋮"}</span>
        </Button>

        {open &&
          !unavailable &&
          createPortal(
            <div
              ref={menuRef}
              id={menuId}
              role="menu"
              aria-label={label}
              aria-orientation="vertical"
              className={cn(
                "fixed z-popover max-h-[calc(100dvh-1rem)] w-56 max-w-[calc(100vw-1rem)] overflow-y-auto border border-border bg-surface-raised",
                contextTitle ? "record-action-menu" : "rounded-lg p-1 shadow-lg",
              )}
              style={{ ...position, width: contextTitle ? RECORD_MENU_WIDTH : MENU_WIDTH }}
              onKeyDown={moveMenuFocus}
              onClick={(event) => event.stopPropagation()}
              onContextMenu={(event) => event.stopPropagation()}
            >
              {contextTitle ? (
                <div className="record-action-header">
                  {contextIcon ? (
                    <span className="record-action-icon-badge" aria-hidden="true">
                      {contextIcon}
                    </span>
                  ) : null}
                  <div className="min-w-0">
                    <p className="record-action-title" title={contextTitle}>
                      {contextTitle}
                    </p>
                    {contextDetail ? (
                      <p className="record-action-detail" title={contextDetail}>
                        {contextDetail}
                      </p>
                    ) : null}
                  </div>
                </div>
              ) : null}
              {items.map((item, index) => (
                <div
                  key={item.id ?? item.label}
                  className={cn(
                    item.tone === "danger" &&
                      index > 0 &&
                      (!contextTitle || items[index - 1]?.tone !== "danger") &&
                      "mt-1 border-t border-border pt-1",
                  )}
                >
                  {contextTitle && item.group && item.group !== items[index - 1]?.group ? (
                    <p role="presentation" className="record-action-group">
                      {item.group}
                    </p>
                  ) : null}
                  <Button
                    role="menuitem"
                    tabIndex={-1}
                    variant="ghost"
                    size="sm"
                    className={cn(
                      "w-full justify-start px-3 font-medium",
                      contextTitle && "record-action-row",
                      contextTitle && item.id === "open" && "record-action-primary",
                      contextTitle && item.tone === "danger" && "record-action-danger",
                      item.tone === "danger" && "text-danger hover:bg-danger/10",
                    )}
                    onClick={() => {
                      closeMenu();
                      sourceRef.current?.focus({ preventScroll: true });
                      item.onSelect();
                    }}
                  >
                    {item.icon ? (
                      <span aria-hidden="true" className="shrink-0">
                        {item.icon}
                      </span>
                    ) : null}
                    <span>{item.label}</span>
                  </Button>
                </div>
              ))}
              {onConfigure ? (
                <div
                  className={cn(
                    "mt-1 border-t border-border pt-1",
                    contextTitle && "record-action-footer",
                  )}
                >
                  <Button
                    role="menuitem"
                    tabIndex={-1}
                    variant="ghost"
                    size="sm"
                    className="w-full justify-start px-3 text-foreground-muted"
                    onClick={() => {
                      closeMenu();
                      onConfigure();
                    }}
                  >
                    {contextTitle ? (
                      <svg
                        aria-hidden="true"
                        focusable="false"
                        viewBox="0 0 24 24"
                        className="record-action-icon"
                      >
                        <path
                          d="M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M8 15v6"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.7"
                          strokeLinecap="round"
                        />
                      </svg>
                    ) : null}
                    Настроить меню
                  </Button>
                  {contextTitle ? (
                    <span className="record-action-escape" aria-hidden="true">
                      Esc — закрыть
                    </span>
                  ) : null}
                </div>
              ) : null}
            </div>,
            document.body,
          )}
      </>
    );
  },
);

function setForwardedRef<T>(ref: ForwardedRef<T>, value: T | null): void {
  if (typeof ref === "function") {
    ref(value);
    return;
  }
  if (ref) (ref as { current: T | null }).current = value;
}
