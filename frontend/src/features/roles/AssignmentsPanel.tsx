import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Badge, Button, ConfirmDialog, FormError, Label, Select, Switch } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";

import {
  useCreateAssignment,
  useRevokeAssignment,
  useRolesQuery,
} from "./queries";
import { type UserWithAssignments } from "./types";

const schema = z.object({
  role_id: z.string().min(1, "Выберите роль"),
  branch_id: z.string(),
  password_required: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

export function AssignmentsPanel({
  user,
  onClose,
}: {
  user: UserWithAssignments;
  onClose: () => void;
}): JSX.Element {
  const roles = useRolesQuery();
  const branches = useBranchesQuery(true);
  const createAssignment = useCreateAssignment();
  const revokeAssignment = useRevokeAssignment();
  const [topError, setTopError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [pendingRevokeId, setPendingRevokeId] = useState<string | null>(null);
  const [revokeError, setRevokeError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    defaultValues: { role_id: "", branch_id: "", password_required: false },
  });

  const onAdd = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const p = issue.path[0];
        if (typeof p !== "string" || seen.has(p)) continue;
        seen.add(p);
        form.setError(p as keyof FormValues, { message: issue.message });
      }
      return;
    }
    setTopError(null);
    const d = parsed.data;
    try {
      await createAssignment.mutateAsync({
        userId: user.id,
        payload: {
          role_id: d.role_id,
          branch_id: d.branch_id === "" ? null : d.branch_id,
          password_required: d.password_required,
        },
      });
      form.reset({ role_id: "", branch_id: "", password_required: false });
      setAddOpen(false);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось назначить роль"));
    }
  });

  const confirmRevoke = async () => {
    if (!pendingRevokeId) return;
    setRevokeError(null);
    try {
      await revokeAssignment.mutateAsync({ userId: user.id, assignmentId: pendingRevokeId });
      setPendingRevokeId(null);
    } catch (err) {
      setRevokeError(describeApiError(err, "Не удалось отозвать"));
    }
  };

  const roleName = (roleId: string) => roles.data?.find((r) => r.id === roleId)?.name ?? roleId.slice(0, 8);
  const branchName = (branchId: string | null) =>
    branchId ? branches.data?.find((b) => b.id === branchId)?.name ?? branchId.slice(0, 8) : "все точки";

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm text-foreground-muted">Назначения роли</p>
        {user.assignments.length === 0 ? (
          <p className="text-sm italic text-foreground-muted">Ролей пока нет</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {user.assignments.map((a) => (
              <li
                key={a.id}
                className="flex items-center justify-between rounded-md border border-border bg-surface px-3 py-2"
              >
                <div className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{roleName(a.role_id)}</span>
                    <Badge tone={a.is_active ? "success" : "neutral"}>
                      {a.is_active ? "активна" : "отозвана"}
                    </Badge>
                    {a.password_required && <Badge tone="info">пароль</Badge>}
                  </div>
                  <p className="text-xs text-foreground-muted">{branchName(a.branch_id)}</p>
                </div>
                {a.is_active && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setRevokeError(null);
                      setPendingRevokeId(a.id);
                    }}
                    isLoading={revokeAssignment.isPending}
                  >
                    Отозвать
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {addOpen ? (
        <form onSubmit={onAdd} noValidate className="space-y-3 rounded-md border border-border bg-foreground/[0.03] p-3">
          <div>
            <Label htmlFor="role_id">Роль</Label>
            <Select
              id="role_id"
              invalid={Boolean(form.formState.errors.role_id)}
              {...form.register("role_id")}
            >
              <option value="">— выберите —</option>
              {roles.data
                ?.filter((r) => r.is_active)
                .map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name} (уровень {r.level})
                  </option>
                ))}
            </Select>
            <FormError>{form.formState.errors.role_id?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="branch_id">Точка</Label>
            <Select id="branch_id" {...form.register("branch_id")}>
              <option value="">— любая —</option>
              {branches.data?.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </Select>
          </div>
          <Switch label="Требовать пароль" {...form.register("password_required")} />
          {topError && <p className="text-sm text-danger">{topError}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setAddOpen(false)}>
              Отмена
            </Button>
            <Button type="submit" size="sm" isLoading={form.formState.isSubmitting}>
              Добавить
            </Button>
          </div>
        </form>
      ) : (
        <Button variant="secondary" onClick={() => setAddOpen(true)}>
          + Назначить роль
        </Button>
      )}

      <div className="flex justify-end">
        <Button variant="ghost" onClick={onClose}>
          Закрыть
        </Button>
      </div>

      <ConfirmDialog
        open={pendingRevokeId !== null}
        title="Отозвать роль"
        message={
          <>
            Роль перестанет действовать для этого пользователя.
            {revokeError && <span className="mt-2 block text-danger">{revokeError}</span>}
          </>
        }
        confirmLabel="Отозвать"
        variant="danger"
        isLoading={revokeAssignment.isPending}
        onConfirm={() => void confirmRevoke()}
        onCancel={() => {
          setPendingRevokeId(null);
          setRevokeError(null);
        }}
      />
    </div>
  );
}
