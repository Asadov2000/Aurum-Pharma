import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  FormError,
  Input,
  Label,
  PageHeader,
  Pagination,
} from "@/components/ui";
import { employeeInitials } from "@/features/roles/userPresentation";
import { cn } from "@/lib/utils";

import { ReadError } from "./PayHistory";
import { useStaffPayAccess, useStaffPayEmployees } from "./queries";
import { StaffPayPanel } from "./StaffPayPanel";
import { type StaffPayEmployee } from "./types";

export function StaffPayPage(): JSX.Element {
  const access = useStaffPayAccess();
  if (!access.view) return <p role="alert">Нет доступа к выплатам сотрудников.</p>;
  return <Management key={access.scope.join(":")} />;
}

function Management(): JSX.Element {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [employee, setEmployee] = useState<StaffPayEmployee | null>(null);
  const query = useStaffPayEmployees(q, page);
  const form = useForm<{ q: string }>({ defaultValues: { q: "" } });
  const submit = form.handleSubmit(({ q: value }) => {
    const result = z.string().trim().max(100, "Не более 100 символов").safeParse(value);
    if (!result.success) {
      form.setError("q", { message: result.error.issues[0]?.message });
      return;
    }
    form.clearErrors();
    setQ(result.data);
    setPage(1);
    setEmployee(null);
  });
  return (
    <div className="mx-auto w-full max-w-7xl space-y-5" data-testid="staff-pay-management">
      <PageHeader
        title="Выплаты сотрудникам"
        showTitleOnDesktop
        description="Укажите сумму за период и запишите уже выданные наличные."
        actions={<Badge>Приватные сведения</Badge>}
      />
      <div className="grid min-w-0 items-start gap-5 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>Выберите сотрудника</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <form className="space-y-3" onSubmit={(event) => void submit(event)}>
              <div>
                <Label htmlFor="pay-employee">Найти сотрудника</Label>
                <Input
                  id="pay-employee"
                  maxLength={100}
                  autoComplete="off"
                  placeholder="Имя сотрудника"
                  {...form.register("q")}
                />
                <FormError>{form.formState.errors.q?.message}</FormError>
              </div>
              <Button
                type="submit"
                variant="secondary"
                className="w-full"
                disabled={query.isFetching}
              >
                Найти
              </Button>
            </form>
            {query.isPending ? (
              <p role="status" className="text-sm text-foreground-muted">
                Загрузка сотрудников…
              </p>
            ) : query.isError ? (
              <ReadError error={query.error} retry={() => void query.refetch()} />
            ) : query.data ? (
              <>
                {query.data.items.length === 0 ? (
                  <p className="text-sm text-foreground-muted">Сотрудники не найдены.</p>
                ) : (
                  <ul className="space-y-2" aria-label="Выбрать сотрудника">
                    {query.data.items.map((item) => (
                      <li key={item.id}>
                        <Button
                          variant="ghost"
                          className={cn(
                            "h-auto w-full justify-start whitespace-normal rounded-lg border py-3 text-left",
                            employee?.id === item.id
                              ? "border-primary/25 bg-primary/5"
                              : "border-transparent",
                          )}
                          aria-pressed={employee?.id === item.id}
                          aria-label={item.full_name}
                          onClick={() => setEmployee(item)}
                        >
                          <span className="flex items-center gap-3">
                            <span
                              aria-hidden="true"
                              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-primary/10 bg-primary/5 text-xs text-primary"
                            >
                              {employeeInitials(item.full_name)}
                            </span>
                            <span className="min-w-0">
                              <span className="block break-words text-sm font-semibold">
                                {item.full_name}
                              </span>
                              <span className="mt-1 block text-xs font-normal text-foreground-muted">
                                {item.status === "active" ? "Активен" : "Неактивен"}
                              </span>
                            </span>
                          </span>
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
                <Pagination
                  page={page}
                  pageSize={query.data.page_size}
                  total={query.data.total}
                  onPage={(nextPage) => {
                    setPage(nextPage);
                    setEmployee(null);
                  }}
                />
              </>
            ) : null}
            <p className="border-t border-border pt-4 text-xs leading-relaxed text-foreground-muted">
              Каждый сотрудник видит только свои сведения в разделе «Моя работа».
            </p>
          </CardContent>
        </Card>
        {employee ? (
          <section className="min-w-0 space-y-5" aria-label="Сведения выбранного сотрудника">
            {!query.isError && !query.isPending ? (
              <Card className="border-l-[3px] border-l-primary">
                <CardContent className="flex items-center gap-3">
                  <span
                    aria-hidden="true"
                    className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/5 font-semibold text-primary"
                  >
                    {employeeInitials(employee.full_name)}
                  </span>
                  <div className="min-w-0">
                    <h2 className="break-words text-lg font-semibold">{employee.full_name}</h2>
                    <p className="mt-1 text-xs text-foreground-muted">
                      Сведения выбранного сотрудника · Выдачи наличными
                    </p>
                    {employee.status !== "active" && (
                      <p className="mt-2 text-xs text-foreground-muted">
                        Неактивный сотрудник · История выдач сохраняется.
                      </p>
                    )}
                  </div>
                </CardContent>
              </Card>
            ) : null}
            <StaffPayPanel
              key={employee.id}
              own={false}
              employeeId={employee.id}
              contextUnavailable={query.isError || query.isPending}
            />
          </section>
        ) : (
          <Card>
            <CardContent className="flex min-h-52 flex-col justify-center space-y-2 text-center">
              <h2 className="text-base font-semibold">Сведения сотрудника</h2>
              <p className="text-sm text-foreground-muted">
                {query.isError
                  ? "Обновите список сотрудников, чтобы открыть сведения."
                  : "Выберите сотрудника, чтобы открыть сведения за периоды."}
              </p>
            </CardContent>
          </Card>
        )}
      </div>
      <p className="text-xs leading-relaxed text-foreground-muted">
        Суммы вносит руководитель вручную. Этот раздел не рассчитывает зарплату по сменам и не
        списывает деньги из кассы.
      </p>
    </div>
  );
}
