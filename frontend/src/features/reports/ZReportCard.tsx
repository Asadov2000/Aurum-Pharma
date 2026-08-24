import { Badge, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { type ZReport } from "@/features/pos/types";

const METHOD_LABELS = {
  cash: "Наличные",
  card: "Карта",
  qr: "QR-код",
  bank_transfer: "Банковский перевод",
  mixed: "Смешанная оплата",
} as const;

export function ZReportCard({
  report,
  branchName,
  registerName,
  cashierName,
  currency,
  reportTimezone,
}: {
  report: ZReport;
  branchName?: string;
  registerName?: string;
  cashierName?: string | null;
  currency: string;
  reportTimezone: string;
}): JSX.Element {
  // totals is a free-form dict; we extract known fields and pass the
  // rest through so unrecognised keys still render rather than disappear.
  const totals = report.totals ?? {};
  const totalSales = pickNumber(totals, "sales_total") ?? pickNumber(totals, "total_sales") ?? 0;
  const totalReturns =
    pickNumber(totals, "returns_total") ?? pickNumber(totals, "total_returns") ?? 0;
  const totalDiscounts =
    pickNumber(totals, "discounts_total") ?? pickNumber(totals, "total_discounts") ?? 0;

  const cashDiff = Number(report.closing_difference ?? 0);
  const diffTone = cashDiff === 0 ? "success" : cashDiff > 0 ? "info" : "danger";
  const diffLabel = cashDiff === 0 ? "сходится" : cashDiff > 0 ? "избыток" : "недостача";

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <section className="border-b border-border p-4" aria-labelledby="z-report-shift-title">
        <h3 id="z-report-shift-title" className="mb-3 text-sm font-semibold text-foreground">
          Смена
        </h3>
        <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-3">
          <Field
            label="Открыта"
            value={new Date(report.opened_at).toLocaleString("ru-RU", {
              timeZone: reportTimezone,
            })}
          />
          <Field
            label="Закрыта"
            value={
              report.closed_at
                ? new Date(report.closed_at).toLocaleString("ru-RU", {
                    timeZone: reportTimezone,
                  })
                : "—"
            }
          />
          {branchName && <Field label="Точка" value={branchName} />}
          <Field
            label="Касса"
            value={registerName ?? report.register_id.slice(0, 8)}
            mono={!registerName}
          />
          <Field
            label="Кассир"
            value={cashierName ?? report.cashier_user_id.slice(0, 8)}
            mono={!cashierName}
          />
          <Field
            label="Продажи / Возвраты"
            value={`${report.sales_count} / ${report.returns_count}`}
          />
        </div>
      </section>

      <section className="border-b border-border p-4" aria-labelledby="z-report-cash-title">
        <h3 id="z-report-cash-title" className="mb-3 text-sm font-semibold text-foreground">
          Касса
        </h3>
        <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
          <Field label="На начало" value={formatMoney(report.opening_cash, currency)} mono />
          <Field
            label="Ожидалось"
            value={formatMoney(report.closing_cash_expected, currency)}
            mono
          />
          <Field
            label="Фактически"
            value={formatMoney(report.closing_cash_actual, currency)}
            mono
          />
          <div>
            <p className="text-xs text-foreground-muted">Разница</p>
            <div className="flex items-center gap-2">
              <p className="font-mono">{formatMoney(report.closing_difference, currency)}</p>
              <Badge tone={diffTone}>{diffLabel}</Badge>
            </div>
          </div>
        </div>
      </section>

      <section className="space-y-4 p-4" aria-labelledby="z-report-turnover-title">
        <h3 id="z-report-turnover-title" className="text-sm font-semibold text-foreground">
          Обороты
        </h3>
        <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-3">
          <Field label="Продажи всего" value={formatMoney(totalSales, currency)} mono />
          <Field label="Скидки" value={formatMoney(totalDiscounts, currency)} mono />
          <Field label="Возвраты всего" value={formatMoney(totalReturns, currency)} mono />
        </div>

        <ByMethodTable totals={totals} currency={currency} />
      </section>
    </div>
  );
}

function ByMethodTable({
  totals,
  currency,
}: {
  totals: Record<string, unknown>;
  currency: string;
}): JSX.Element | null {
  // The backend service may put per-method amounts under different keys.
  // We probe a few common shapes; if nothing matches, hide the table.
  const byMethod = (totals["by_method"] ?? totals["payments"] ?? totals["methods"]) as
    | Record<string, unknown>
    | undefined;
  if (!byMethod || typeof byMethod !== "object") return null;

  const rows = Object.keys(METHOD_LABELS)
    .map((method) => ({
      method: method as keyof typeof METHOD_LABELS,
      amount: pickNumber(byMethod, method) ?? 0,
    }))
    .filter((r) => r.amount !== 0);

  if (rows.length === 0) return null;

  return (
    <div>
      <p className="mb-1 text-sm font-medium text-foreground-secondary">По способам оплаты</p>
      <Table>
        <THead>
          <TR>
            <TH>Способ</TH>
            <TH className="text-right">Сумма</TH>
          </TR>
        </THead>
        <TBody>
          {rows.map((r) => (
            <TR key={r.method}>
              <TD>{METHOD_LABELS[r.method]}</TD>
              <TD className="text-right font-mono">{formatMoney(r.amount, currency)}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

function pickNumber(obj: Record<string, unknown>, key: string): number | null {
  const v = obj[key];
  if (typeof v === "number") return v;
  if (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v))) return Number(v);
  return null;
}

function formatMoney(v: string | number | null | undefined, currency: string): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (Number.isNaN(n)) return String(v);
  return `${n.toLocaleString("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${currency}`;
}

function Field({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}): JSX.Element {
  return (
    <div>
      <p className="text-xs text-foreground-muted">{label}</p>
      <p className={mono ? "font-mono" : ""}>{value}</p>
    </div>
  );
}
