import { Badge } from "@/components/ui";

import { dispensingLabel, storageLabel } from "./labels";
import { type CatalogItem } from "./types";

function Detail({
  label,
  value,
}: {
  label: string;
  value: string | null | undefined;
}): JSX.Element {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-foreground-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm text-foreground">{value || "—"}</dd>
    </div>
  );
}

export function CatalogItemDetails({ item }: { item: CatalogItem }): JSX.Element {
  const status = item.deleted_at ? "В архиве" : item.is_active ? "Активна" : "Отключена";
  const tone = item.deleted_at ? "neutral" : item.is_active ? "success" : "warning";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
        <div className="min-w-0">
          <p className="break-words text-lg font-semibold text-foreground">{item.brand_name}</p>
          <p className="mt-1 break-words text-sm text-foreground-muted">
            {[item.form, item.dosage, item.pack_size].filter(Boolean).join(" · ") ||
              "Форма выпуска не указана"}
          </p>
        </div>
        <Badge tone={tone}>{status}</Badge>
      </div>

      <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
        <Detail label="МНН" value={item.inn} />
        <Detail label="Производитель" value={item.manufacturer} />
        <Detail label="Категория" value={item.category} />
        <Detail label="ATX-код" value={item.atx_code} />
        <Detail label="Условия отпуска" value={dispensingLabel[item.dispensing_type]} />
        <Detail label="Хранение" value={storageLabel[item.storage_type]} />
        <Detail
          label="Базовая цена"
          value={item.base_price ? `${Number(item.base_price).toFixed(2)} ${item.currency}` : null}
        />
        <Detail
          label="Доступный остаток"
          value={
            item.stock_available === undefined || item.stock_available === null
              ? null
              : `${item.stock_available} уп.`
          }
        />
      </dl>
    </div>
  );
}
