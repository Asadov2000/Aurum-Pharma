import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Badge,
  Button,
  Card,
  CardContent,
  FormError,
  Input,
  Label,
  PageHeader,
  Pagination,
} from "@/components/ui";

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
    <div className="max-w-5xl space-y-5">
      <PageHeader
        title="Выплаты сотрудникам"
        showTitleOnDesktop
        description="Ручные сведения о суммах за период, авансах и выдачах наличными."
      />
      <Card>
        <CardContent className="space-y-4 pt-5">
          <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => void submit(event)}>
            <div className="min-w-0 flex-1">
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
            <Button type="submit" variant="secondary">
              Найти
            </Button>
          </form>
          {query.isPending ? (
            <p role="status">Загрузка сотрудников…</p>
          ) : query.isError ? (
            <ReadError error={query.error} retry={() => void query.refetch()} />
          ) : query.data ? (
            <>
              {query.data.items.length === 0 ? (
                <p>Сотрудники не найдены.</p>
              ) : (
                <ul className="grid gap-2 sm:grid-cols-2" aria-label="Выбрать сотрудника">
                  {query.data.items.map((item) => (
                    <li key={item.id}>
                      <Button
                        variant="secondary"
                        className="h-auto w-full justify-between gap-2 whitespace-normal py-3 text-left"
                        aria-pressed={employee?.id === item.id}
                        onClick={() => setEmployee(item)}
                      >
                        <span className="min-w-0 break-words">{item.full_name}</span>
                        {item.status !== "active" ? <Badge tone="neutral">Неактивен</Badge> : null}
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              <Pagination
                page={page}
                pageSize={query.data.page_size}
                total={query.data.total}
                onPage={setPage}
              />
            </>
          ) : null}
        </CardContent>
      </Card>
      {employee && !query.isError ? (
        <section className="space-y-3" aria-label="Сведения выбранного сотрудника">
          <h2 className="break-words text-lg font-medium">{employee.full_name}</h2>
          <StaffPayPanel key={employee.id} own={false} employeeId={employee.id} />
        </section>
      ) : (
        <p className="text-sm text-foreground-muted">
          Выберите сотрудника, чтобы открыть сведения за периоды.
        </p>
      )}
    </div>
  );
}
