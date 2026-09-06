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
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-foreground-secondary">
      <span>
        {total !== undefined ? (
          <>
            Всего: <span className="font-medium text-foreground">{total}</span>
          </>
        ) : null}
      </span>
      <div className="ml-auto flex items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          aria-label="Назад"
          title="Предыдущая страница"
          disabled={page <= 1}
          onClick={() => onPage(Math.max(1, page - 1))}
        >
          <span aria-hidden="true">←</span>
          <span className="hidden sm:inline">Назад</span>
        </Button>
        <span className="min-w-20 text-center text-xs">
          {totalPages !== undefined ? `${page} из ${totalPages}` : `Стр. ${page}`}
        </span>
        <Button
          variant="secondary"
          size="sm"
          aria-label="Вперёд"
          title="Следующая страница"
          disabled={isLast}
          onClick={() => onPage(page + 1)}
        >
          <span className="hidden sm:inline">Вперёд</span>
          <span aria-hidden="true">→</span>
        </Button>
      </div>
    </div>
  );
}
