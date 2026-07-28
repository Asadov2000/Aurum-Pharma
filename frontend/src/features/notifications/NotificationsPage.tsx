import { useState } from "react";

import { PageHeader, SegmentedControl } from "@/components/ui";

import { Inbox } from "./Inbox";
import { SubscriptionsForm } from "./SubscriptionsForm";

type Tab = "inbox" | "subscriptions";

export function NotificationsPage(): JSX.Element {
  const [tab, setTab] = useState<Tab>("inbox");

  return (
    <div className="space-y-4">
      <PageHeader title="Уведомления" />

      <SegmentedControl
        value={tab}
        options={[
          { value: "inbox", label: "Инбокс" },
          { value: "subscriptions", label: "Подписки" },
        ]}
        onChange={setTab}
        label="Уведомления"
      />

      {tab === "inbox" ? <Inbox /> : <SubscriptionsForm />}
    </div>
  );
}
