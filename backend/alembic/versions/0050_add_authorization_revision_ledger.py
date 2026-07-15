"""security: add transactionally monotonic authorization revisions

The revision ledger is the source of truth for future offline-auth grants and
revision-keyed authorization caches. It does not enable offline POS access.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0050"
down_revision: str | Sequence[str] | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_BIGINT = "9223372036854775807"

POLICY_REVISION_FUNCTIONS = (
    "public.ensure_authorization_policy_revision(UUID)",
    "public.bump_authorization_policy_revision(UUID)",
    "public.bump_all_authorization_policy_revisions()",
    "public.bump_authorization_subject_revision(UUID, UUID)",
    "public.bump_authorization_subjects_for_user(UUID)",
    "public.trg_authorization_tenant_created()",
    "public.trg_authorization_policy_mutation()",
    "public.trg_authorization_permission_mutation()",
    "public.trg_authorization_role_permission_mutation()",
    "public.trg_authorization_assignment_mutation()",
    "public.trg_authorization_user_status_mutation()",
)


ENSURE_POLICY_FUNCTION_SQL = """
CREATE FUNCTION public.ensure_authorization_policy_revision(
  p_tenant_id UUID
) RETURNS VOID AS $$
BEGIN
  IF p_tenant_id IS NULL OR NOT EXISTS (
    SELECT 1 FROM public.tenant WHERE id = p_tenant_id
  ) THEN
    RETURN;
  END IF;

  INSERT INTO public.authorization_policy_revision (
    tenant_id,
    revision,
    created_at,
    updated_at,
    created_by,
    updated_by
  ) VALUES (
    p_tenant_id,
    1,
    now(),
    now(),
    public.current_app_user_id(),
    public.current_app_user_id()
  ) ON CONFLICT (tenant_id) DO NOTHING;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


BUMP_POLICY_FUNCTION_SQL = f"""
CREATE FUNCTION public.bump_authorization_policy_revision(
  p_tenant_id UUID
) RETURNS VOID AS $$
BEGIN
  IF p_tenant_id IS NULL OR NOT EXISTS (
    SELECT 1 FROM public.tenant WHERE id = p_tenant_id
  ) THEN
    RETURN;
  END IF;

  PERFORM public.ensure_authorization_policy_revision(p_tenant_id);

  UPDATE public.authorization_policy_revision
  SET revision = revision + 1,
      updated_at = now(),
      updated_by = public.current_app_user_id()
  WHERE tenant_id = p_tenant_id
    AND revision < {MAX_BIGINT};

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Authorization policy revision is exhausted'
      USING ERRCODE = '22003';
  END IF;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


BUMP_ALL_POLICY_FUNCTION_SQL = f"""
CREATE FUNCTION public.bump_all_authorization_policy_revisions()
RETURNS VOID AS $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public.authorization_policy_revision
    WHERE revision >= {MAX_BIGINT}
  ) THEN
    RAISE EXCEPTION 'Authorization policy revision is exhausted'
      USING ERRCODE = '22003';
  END IF;

  UPDATE public.authorization_policy_revision
  SET revision = revision + 1,
      updated_at = now(),
      updated_by = public.current_app_user_id();
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


BUMP_SUBJECT_FUNCTION_SQL = f"""
CREATE FUNCTION public.bump_authorization_subject_revision(
  p_tenant_id UUID,
  p_user_id UUID
) RETURNS VOID AS $$
BEGIN
  IF p_tenant_id IS NULL OR p_user_id IS NULL
     OR NOT EXISTS (SELECT 1 FROM public.tenant WHERE id = p_tenant_id)
     OR NOT EXISTS (SELECT 1 FROM public.app_user WHERE id = p_user_id)
  THEN
    RETURN;
  END IF;

  INSERT INTO public.authorization_subject_revision (
    tenant_id,
    user_id,
    revision,
    created_at,
    updated_at,
    created_by,
    updated_by
  ) VALUES (
    p_tenant_id,
    p_user_id,
    1,
    now(),
    now(),
    public.current_app_user_id(),
    public.current_app_user_id()
  ) ON CONFLICT (tenant_id, user_id) DO NOTHING;

  UPDATE public.authorization_subject_revision
  SET revision = revision + 1,
      updated_at = now(),
      updated_by = public.current_app_user_id()
  WHERE tenant_id = p_tenant_id
    AND user_id = p_user_id
    AND revision < {MAX_BIGINT};

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Authorization subject revision is exhausted'
      USING ERRCODE = '22003';
  END IF;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


BUMP_SUBJECTS_FOR_USER_FUNCTION_SQL = """
CREATE FUNCTION public.bump_authorization_subjects_for_user(
  p_user_id UUID
) RETURNS VOID AS $$
DECLARE
  v_tenant_id UUID;
BEGIN
  FOR v_tenant_id IN
    SELECT DISTINCT assignment.tenant_id
    FROM public.user_assignment AS assignment
    WHERE assignment.user_id = p_user_id
  LOOP
    PERFORM public.bump_authorization_subject_revision(v_tenant_id, p_user_id);
  END LOOP;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


TENANT_CREATED_TRIGGER_SQL = """
CREATE FUNCTION public.trg_authorization_tenant_created()
RETURNS TRIGGER AS $$
BEGIN
  PERFORM public.ensure_authorization_policy_revision(NEW.id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


POLICY_MUTATION_TRIGGER_SQL = """
CREATE FUNCTION public.trg_authorization_policy_mutation()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM public.bump_authorization_policy_revision(OLD.tenant_id);
    RETURN OLD;
  END IF;

  PERFORM public.bump_authorization_policy_revision(NEW.tenant_id);
  IF TG_OP = 'UPDATE' AND OLD.tenant_id IS DISTINCT FROM NEW.tenant_id THEN
    PERFORM public.bump_authorization_policy_revision(OLD.tenant_id);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


PERMISSION_MUTATION_TRIGGER_SQL = """
CREATE FUNCTION public.trg_authorization_permission_mutation()
RETURNS TRIGGER AS $$
BEGIN
  PERFORM public.bump_all_authorization_policy_revisions();
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ROLE_PERMISSION_MUTATION_TRIGGER_SQL = """
CREATE FUNCTION public.trg_authorization_role_permission_mutation()
RETURNS TRIGGER AS $$
DECLARE
  v_old_tenant_id UUID;
  v_new_tenant_id UUID;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    SELECT tenant_id INTO v_old_tenant_id
    FROM public.role
    WHERE id = OLD.role_id;
    PERFORM public.bump_authorization_policy_revision(v_old_tenant_id);
  END IF;

  IF TG_OP <> 'DELETE' THEN
    SELECT tenant_id INTO v_new_tenant_id
    FROM public.role
    WHERE id = NEW.role_id;
    IF v_new_tenant_id IS DISTINCT FROM v_old_tenant_id THEN
      PERFORM public.bump_authorization_policy_revision(v_new_tenant_id);
    END IF;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ASSIGNMENT_MUTATION_TRIGGER_SQL = """
CREATE FUNCTION public.trg_authorization_assignment_mutation()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM public.bump_authorization_subject_revision(OLD.tenant_id, OLD.user_id);
    RETURN OLD;
  END IF;

  PERFORM public.bump_authorization_subject_revision(NEW.tenant_id, NEW.user_id);
  IF TG_OP = 'UPDATE' THEN
    IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
       OR OLD.user_id IS DISTINCT FROM NEW.user_id
    THEN
      PERFORM public.bump_authorization_subject_revision(OLD.tenant_id, OLD.user_id);
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


USER_STATUS_MUTATION_TRIGGER_SQL = """
CREATE FUNCTION public.trg_authorization_user_status_mutation()
RETURNS TRIGGER AS $$
BEGIN
  PERFORM public.bump_authorization_subjects_for_user(NEW.id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _secure_functions() -> None:
    for signature in POLICY_REVISION_FUNCTIONS:
        op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")


def _create_meta_triggers(table: str) -> None:
    op.execute(f"""
        CREATE TRIGGER trg_{table}_created
        BEFORE INSERT ON public.{table}
        FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
        """)
    op.execute(f"""
        CREATE TRIGGER trg_{table}_updated
        BEFORE UPDATE ON public.{table}
        FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta()
        """)


def _create_rls(table: str, *, subject: bool = False) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    predicate = "tenant_id = public.current_tenant_id()"
    if subject:
        predicate += " AND user_id = public.current_app_user_id()"
    op.execute(f"""
        CREATE POLICY tenant_isolation ON public.{table}
        USING ({predicate})
        WITH CHECK ({predicate})
        """)


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE public.authorization_policy_revision (
          tenant_id  UUID PRIMARY KEY REFERENCES public.tenant(id) ON DELETE CASCADE,
          revision   BIGINT NOT NULL DEFAULT 1
                     CHECK (revision BETWEEN 1 AND {MAX_BIGINT}),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL
        )
        """)
    op.execute(f"""
        CREATE TABLE public.authorization_subject_revision (
          tenant_id  UUID NOT NULL REFERENCES public.tenant(id) ON DELETE CASCADE,
          user_id    UUID NOT NULL REFERENCES public.app_user(id) ON DELETE CASCADE,
          revision   BIGINT NOT NULL DEFAULT 1
                     CHECK (revision BETWEEN 1 AND {MAX_BIGINT}),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          PRIMARY KEY (tenant_id, user_id)
        )
        """)

    op.execute("""
        INSERT INTO public.authorization_policy_revision (tenant_id)
        SELECT id FROM public.tenant
        ON CONFLICT (tenant_id) DO NOTHING
        """)
    op.execute("""
        INSERT INTO public.authorization_subject_revision (tenant_id, user_id)
        SELECT DISTINCT tenant_id, user_id
        FROM public.user_assignment
        ON CONFLICT (tenant_id, user_id) DO NOTHING
        """)

    _create_meta_triggers("authorization_policy_revision")
    _create_meta_triggers("authorization_subject_revision")
    _create_rls("authorization_policy_revision")
    _create_rls("authorization_subject_revision", subject=True)

    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.authorization_policy_revision, "
        "public.authorization_subject_revision FROM PUBLIC, aurum_app"
    )
    op.execute(
        "GRANT SELECT ON TABLE public.authorization_policy_revision, "
        "public.authorization_subject_revision TO aurum_app"
    )

    for function_sql in (
        ENSURE_POLICY_FUNCTION_SQL,
        BUMP_POLICY_FUNCTION_SQL,
        BUMP_ALL_POLICY_FUNCTION_SQL,
        BUMP_SUBJECT_FUNCTION_SQL,
        BUMP_SUBJECTS_FOR_USER_FUNCTION_SQL,
        TENANT_CREATED_TRIGGER_SQL,
        POLICY_MUTATION_TRIGGER_SQL,
        PERMISSION_MUTATION_TRIGGER_SQL,
        ROLE_PERMISSION_MUTATION_TRIGGER_SQL,
        ASSIGNMENT_MUTATION_TRIGGER_SQL,
        USER_STATUS_MUTATION_TRIGGER_SQL,
    ):
        op.execute(function_sql)
    _secure_functions()

    op.execute("""
        CREATE TRIGGER trg_authorization_tenant_created
        AFTER INSERT ON public.tenant
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_tenant_created()
        """)
    op.execute("""
        CREATE TRIGGER trg_authorization_role_policy
        AFTER INSERT OR DELETE OR UPDATE OF tenant_id, level, is_active
        ON public.role
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_policy_mutation()
        """)
    op.execute("""
        CREATE TRIGGER trg_authorization_branch_policy
        AFTER DELETE OR UPDATE OF tenant_id, is_active ON public.branch
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_policy_mutation()
        """)
    op.execute("""
        CREATE TRIGGER trg_authorization_register_policy
        AFTER DELETE OR UPDATE OF tenant_id, branch_id, is_active ON public.register
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_policy_mutation()
        """)
    op.execute("""
        CREATE TRIGGER trg_authorization_permission_policy
        AFTER INSERT OR DELETE OR UPDATE OF min_level_required, is_active
        ON public.permission
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_permission_mutation()
        """)
    op.execute("""
        CREATE TRIGGER trg_authorization_role_permission_policy
        AFTER INSERT OR DELETE OR UPDATE ON public.role_permission
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_role_permission_mutation()
        """)
    op.execute("""
        CREATE TRIGGER trg_authorization_assignment_subject
        AFTER INSERT OR DELETE OR UPDATE ON public.user_assignment
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_assignment_mutation()
        """)
    op.execute("""
        CREATE TRIGGER trg_authorization_user_status_subject
        AFTER UPDATE OF status, is_developer, is_administrator, home_tenant_id
        ON public.app_user
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_user_status_mutation()
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_authorization_user_status_subject ON public.app_user")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_authorization_assignment_subject ON public.user_assignment"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_authorization_role_permission_policy ON public.role_permission"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_authorization_permission_policy ON public.permission")
    op.execute("DROP TRIGGER IF EXISTS trg_authorization_register_policy ON public.register")
    op.execute("DROP TRIGGER IF EXISTS trg_authorization_branch_policy ON public.branch")
    op.execute("DROP TRIGGER IF EXISTS trg_authorization_role_policy ON public.role")
    op.execute("DROP TRIGGER IF EXISTS trg_authorization_tenant_created ON public.tenant")

    for signature in reversed(POLICY_REVISION_FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")

    op.execute("DROP TABLE public.authorization_subject_revision")
    op.execute("DROP TABLE public.authorization_policy_revision")
