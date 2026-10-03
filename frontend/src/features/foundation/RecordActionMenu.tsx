import { useEffect, useRef, useState, type Ref } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "@tanstack/react-router";

import { ActionMenu } from "@/components/ui/ActionMenu";
import {
  applyQuickActions,
  type QuickActionMenuPreferences,
  type QuickActionSection,
} from "@/features/settings/quickActions";

export type RecordAction = {
  id: string;
  label: string;
  tone?: "default" | "danger";
} & ({ onSelect: () => void; copyValue?: never } | { copyValue: string; onSelect?: never });

export function RecordActionMenu({
  section,
  recordKey,
  title,
  detail,
  label,
  items,
  config,
  scope,
  disabled = false,
  isLoading = false,
  triggerRef,
}: {
  section: QuickActionSection;
  recordKey: string;
  title: string;
  detail?: string;
  label: string;
  items: RecordAction[];
  config: QuickActionMenuPreferences;
  scope: string;
  disabled?: boolean;
  isLoading?: boolean;
  triggerRef?: Ref<HTMLButtonElement>;
}): JSX.Element {
  const navigate = useNavigate();
  const [feedback, setFeedback] = useState<{ scope: string; message: string } | null>(null);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  useEffect(() => {
    if (!feedback) return;
    const timer = window.setTimeout(() => setFeedback(null), 3500);
    return () => window.clearTimeout(timer);
  }, [feedback]);
  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      if (currentScope.current === scope)
        setFeedback({ scope, message: "Скопировано в буфер обмена." });
    } catch {
      if (currentScope.current === scope)
        setFeedback({
          scope,
          message: "Не удалось скопировать. Откройте карточку и скопируйте значение вручную.",
        });
    }
  };
  const visible = applyQuickActions(items, config).map((item) => ({
    id: item.id,
    label: item.label,
    tone: item.tone,
    onSelect: () => {
      if (item.copyValue !== undefined) void copy(item.copyValue);
      else item.onSelect();
    },
  }));
  return (
    <>
      <ActionMenu
        ref={triggerRef}
        label={label}
        items={visible}
        disabled={disabled}
        isLoading={isLoading}
        contextTarget="[data-context-actions]"
        contextEnabled={config.enabled}
        contextTitle={title}
        contextDetail={detail}
        resetKey={`${scope}:${section}:${recordKey}`}
        onConfigure={() =>
          void navigate({ to: "/settings", search: { section: "quick-actions", actions: section } })
        }
      />
      {feedback?.scope === scope
        ? createPortal(
            <p
              role="status"
              className="fixed right-4 bottom-4 z-toast max-w-[calc(100vw-2rem)] rounded-lg border border-border bg-surface-raised px-4 py-3 text-sm text-foreground shadow-lg"
            >
              {feedback.message}
            </p>,
            document.body,
          )
        : null}
    </>
  );
}
