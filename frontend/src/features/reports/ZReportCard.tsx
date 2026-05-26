import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { paymentMethodLabel } from "@/features/pos/labels";
import { type PaymentMethod, type ZReport } from "@/features/pos/types";

const KNOWN_METHODS: PaymentMethod[] = ["cash", "card", "bank_transfer"];

export function ZReportCard({ report }: { report: ZReport }): JSX.Element {
  // totals is a free-form dict; we extract known fields and pass the
  // rest through so unrecognised keys still render rather than disappear.
  const totals = report.totals ?? {};
  const totalSales = pickNumber(totals, "sales_total") ?? pickNumber(totals, "total_sales") ?? 0;
  const totalReturns =
    pickNumber(totals, "returns_total") ?? pickNumber(totals, "total_returns") ?? 0;

  const cashDiff = Number(report.closing_difference ?? 0);
  const diffTone = cashDiff === 0 ? "success" : cashDiff > 0 ? "info" : "danger";
  const diffLabel = cashDiff === 0 ? "сходится" : cashDiff > 0 ? "избыток" : "недостача";

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Смена</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-3">
            <Field
              label="Открыта"
              value={new Date(report.opened_at).toLocaleString("ru-RU")}
            />
            <Field
              label="Закрыта"
              value={
                report.closed_at
                  ? new Date(report.closed_at).toLocaleString("ru-RU")
                  : "—"
              }
            />
            <Field label="Касса" value={report.register_id.slice(0, 8)} mono />
            <Field label="Кассир" value={report.cashier_user_id.slice(0, 8)} mono />
            <Field
              label="Продажи / Возвраты"
              value={`${report.sales_count} / ${report.returns_count}`}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Касса</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
            <Field label="На начало" value={fmt(report.opening_cash)} mono />
            <Field label="Ожидалось" value={fmt(report.closing_cash_expected)} mono />
            <Field label="Фактически" value={fmt(report.closing_cash_actual)} mono />
            <div>
              <p className="text-xs text-slate-500">Разница</p>
              <div className="flex items-center gap-2">
                <p className="font-mono">{fmt(report.closing_difference)}</p>
                <Badge tone={diffTone}>{diffLabel}</Badge>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Обороты</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-3 text-sm">
            <Field label="Продажи всего" value={fmt(totalSales)} mono />
            <Field label="Возвраты всего" value={fmt(totalReturns)} mono />
          </div>

          <ByMethodTable totals={totals} />

          <details className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
            <summary className="cursor-pointer font-medium">Сырой JSON totals</summary>
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-xs">
              {JSON.stringify(totals, null, 2)}
            </pre>
          </details>
        </CardContent>
      </Card>
    </div>
  );
}

function ByMethodTable({ totals }: { totals: Record<string, unknown> }): JSX.Element | null {
  // The backend service may put per-method amounts under different keys.
  // We probe a few common shapes; if nothing matches, hide the table.
  const byMethod = (totals["by_method"] ?? totals["payments"] ?? totals["methods"]) as
    | Record<string, unknown>
    | undefined;
  if (!byMethod || typeof byMethod !== "object") return null;

  const rows = KNOWN_METHODS.map((m) => ({
    method: m,
    amount: pickNumber(byMethod, m) ?? 0,
  })).filter((r) => r.amount !== 0);

  if (rows.length === 0) return null;

  return (
    <div>
      <p className="mb-1 text-sm font-medium text-slate-700">По способам оплаты</p>
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
              <TD>{paymentMethodLabel[r.method]}</TD>
              <TD className="text-right font-mono">{fmt(r.amount)}</TD>
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

function fmt(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toFixed(2);
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
      <p className="text-xs text-slate-500">{label}</p>
      <p className={mono ? "font-mono" : ""}>{value}</p>
    </div>
  );
}
