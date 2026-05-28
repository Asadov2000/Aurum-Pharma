import { useState } from "react";

import { cn } from "@/lib/utils";

import { Inbox } from "./Inbox";
import { SubscriptionsForm } from "./SubscriptionsForm";

type Tab = "inbox" | "subscriptions";

export function NotificationsPage(): JSX.Element {
  const [tab, setTab] = useState<Tab>("inbox");

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-foreground">Уведомления</h1>

      <div className="flex gap-1 border-b border-border">
        <TabButton active={tab === "inbox"} onClick={() => setTab("inbox")}>
          Инбокс
        </TabButton>
        <TabButton active={tab === "subscriptions"} onClick={() => setTab("subscriptions")}>
          Подписки
        </TabButton>
      </div>

      {tab === "inbox" ? <Inbox /> : <SubscriptionsForm />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "border-b-2 px-4 py-2 text-sm font-medium transition-colors",
        active
          ? "border-primary text-foreground"
          : "border-transparent text-foreground-muted hover:text-foreground-secondary",
      )}
    >
      {children}
    </button>
  );
}
