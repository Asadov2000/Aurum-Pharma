import { Button } from "./Button";

interface PaginationProps {
  page: number;
  pageSize: number;
  /** Total row count, when the backend returns it → exact "Стр. X / Y" + "Всего". */
  total?: number;
  /** Fallback when there is no count — next stays enabled while more pages exist. */
  hasMore?: boolean;
  onPage: (page: number) => void;
}

/** One pagination control for every list screen: «← Назад / Вперёд →» with a
 *  total when known. Tokens only, so it follows the theme. */
export function Pagination({
  page,
  pageSize,
  total,
  hasMore,
  onPage,
}: PaginationProps): JSX.Element {
  const totalPages = total !== undefined ? Math.max(1, Math.ceil(total / pageSize)) : undefined;
  const isLast = totalPages !== undefined ? page >= totalPages : !hasMore;

  return (
    <div className="flex items-center justify-between text-sm text-foreground-secondary">
      <span>
        {total !== undefined ? (
          <>
            Всего: <span className="font-medium text-foreground">{total}</span>
          </>
        ) : null}
      </span>
      <div className="flex items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          disabled={page <= 1}
          onClick={() => onPage(Math.max(1, page - 1))}
        >
          ← Назад
        </Button>
        <span>{totalPages !== undefined ? `Стр. ${page} / ${totalPages}` : `Стр. ${page}`}</span>
        <Button
          variant="secondary"
          size="sm"
          disabled={isLast}
          onClick={() => onPage(page + 1)}
        >
          Вперёд →
        </Button>
      </div>
    </div>
  );
}
