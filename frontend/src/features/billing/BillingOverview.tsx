import { Button, Card } from "@/components/ui";
import { formatBillingMoney } from "./format";
import { type TenantFinancialAccount } from "./types";

interface BillingOverviewProps {
  account: TenantFinancialAccount;
  fetching: boolean;
  onRefresh: () => void;
  onOpenInvoice: (invoiceId: string) => void;
  onShowHistory: () => void;
}

export function BillingOverview({
  account,
  fetching,
  onRefresh,
  onShowHistory,
}: BillingOverviewProps): JSX.Element {
  return (
    <Card className="space-y-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">Сохранённые расчёты</h2>
          <p className="mt-1 text-sm text-foreground-muted">
            Подписки отключены. Историческая задолженность не блокирует работу аптеки. Права
            сотрудников и ограничения Aurum сохраняются.
          </p>
        </div>
        <Button variant="secondary" size="sm" isLoading={fetching} onClick={onRefresh}>
          Обновить
        </Button>
      </div>
      <dl className="grid gap-4 sm:grid-cols-2">
        <div>
          <dt className="text-sm text-foreground-muted">Остаток по прежним счетам</dt>
          <dd className="mt-1 font-semibold">
            {formatBillingMoney(account.outstanding_amount, account.currency)}
          </dd>
        </div>
        <div>
          <dt className="text-sm text-foreground-muted">Нераспределённые средства</dt>
          <dd className="mt-1 font-semibold">
            {formatBillingMoney(account.credit_balance, account.currency)}
          </dd>
        </div>
      </dl>
      <Button variant="ghost" size="sm" onClick={onShowHistory}>
        История счетов
      </Button>
    </Card>
  );
}
