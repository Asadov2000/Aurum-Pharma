"""add grant-scoped platform capabilities

Revision ID: 0086
Revises: 0085
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0086"
down_revision: str | None = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PLATFORM_PERMISSION_CODES = (
    "platform.tenants.view",
    "platform.tenants.manage",
    "platform.memberships.manage",
    "platform.ownership.provision",
    "platform.billing.manage",
    "platform.support.use",
    "platform.sync.view",
    "platform.sync.manage",
    "platform.audit.global.view",
    "platform.access.view",
    "platform.access.manage",
)


GUARD_GRANT_PERMISSION_SQL = """
CREATE FUNCTION public.trg_guard_platform_access_grant_permission()
RETURNS TRIGGER AS $$
DECLARE
  v_actor_id UUID;
  v_access_kind TEXT;
  v_grant_status TEXT;
  v_requested_by UUID;
  v_request_reason_code TEXT;
  v_grant_created_at TIMESTAMPTZ;
  v_permission_active BOOLEAN;
  v_permission_target TEXT;
  v_permission_scope TEXT;
  v_developer_grantable BOOLEAN;
  v_administrator_grantable BOOLEAN;
  v_developer_delegable BOOLEAN;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'Platform capability assignments are immutable'
      USING ERRCODE = '42501';
  END IF;

  SELECT
    grant_row.access_kind,
    grant_row.status,
    grant_row.requested_by,
    grant_row.request_reason_code,
    grant_row.created_at
  INTO
    v_access_kind,
    v_grant_status,
    v_requested_by,
    v_request_reason_code,
    v_grant_created_at
  FROM public.platform_access_grant AS grant_row
  WHERE grant_row.id = NEW.grant_id;

  SELECT
    permission.is_active,
    permission.target_role_type,
    permission.scope_type,
    permission.developer_grantable,
    permission.administrator_grantable,
    permission.developer_delegable
  INTO
    v_permission_active,
    v_permission_target,
    v_permission_scope,
    v_developer_grantable,
    v_administrator_grantable,
    v_developer_delegable
  FROM public.permission AS permission
  WHERE permission.code = NEW.permission_code;

  IF v_access_kind IS NULL
    OR v_grant_status NOT IN ('pending', 'active')
    OR v_grant_created_at < pg_catalog.transaction_timestamp()
    OR NOT v_permission_active
    OR v_permission_target <> 'platform'
    OR v_permission_scope <> 'PLATFORM'
    OR NOT v_developer_delegable
    OR (
      v_access_kind = 'developer'
      AND NOT v_developer_grantable
    )
    OR (
      v_access_kind = 'administrator'
      AND NOT v_administrator_grantable
    )
  THEN
    RAISE EXCEPTION 'Capability is outside the platform grant envelope'
      USING ERRCODE = '42501';
  END IF;

  v_actor_id := public.current_app_user_id();

  -- The first trusted Developer is created by the protected bootstrap trigger.
  IF v_actor_id IS NULL
    AND public.is_support_session()
    AND v_requested_by IS NULL
    AND v_request_reason_code = 'bootstrap'
    AND v_access_kind = 'developer'
    AND NEW.created_by IS NULL
  THEN
    RETURN NEW;
  END IF;

  IF v_actor_id IS NULL
    OR NOT public.is_support_session()
    OR public.current_tenant_id() IS NOT NULL
    OR NEW.created_by IS DISTINCT FROM v_actor_id
    OR v_requested_by IS DISTINCT FROM v_actor_id
    OR NOT EXISTS (
      SELECT 1
      FROM public.platform_access_grant AS actor_grant
      JOIN public.app_user AS actor
        ON actor.id = actor_grant.user_id
       AND actor.status = 'active'
      JOIN public.platform_access_grant_permission AS manage_capability
        ON manage_capability.grant_id = actor_grant.id
       AND manage_capability.permission_code = 'platform.access.manage'
      JOIN public.platform_access_grant_permission AS delegated_capability
        ON delegated_capability.grant_id = actor_grant.id
       AND delegated_capability.permission_code = NEW.permission_code
      WHERE actor_grant.user_id = v_actor_id
        AND actor_grant.access_kind = 'developer'
        AND actor_grant.status = 'active'
    )
  THEN
    RAISE EXCEPTION 'Active Developer delegation envelope required'
      USING ERRCODE = '42501';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


SEED_BOOTSTRAP_CAPABILITIES_SQL = """
CREATE FUNCTION public.trg_seed_bootstrap_platform_capabilities()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.requested_by IS NULL
    AND NEW.request_reason_code = 'bootstrap'
    AND NEW.access_kind = 'developer'
    AND NEW.status = 'active'
  THEN
    INSERT INTO public.platform_access_grant_permission (
      grant_id,
      permission_code,
      created_by
    )
    SELECT NEW.id, permission.code, NULL
    FROM public.permission AS permission
    WHERE permission.is_active
      AND permission.target_role_type = 'platform'
      AND permission.scope_type = 'PLATFORM'
      AND permission.developer_grantable
      AND permission.developer_delegable;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


VALIDATE_GRANT_CAPABILITIES_SQL = """
CREATE FUNCTION public.trg_validate_platform_grant_capabilities()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.status NOT IN ('pending', 'active') THEN
    RETURN NEW;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.platform_access_grant_permission AS assignment
    WHERE assignment.grant_id = NEW.id
  ) THEN
    RAISE EXCEPTION 'Active platform grant requires capabilities'
      USING ERRCODE = '23514';
  END IF;

  IF NEW.access_kind = 'developer' AND NOT EXISTS (
    SELECT 1
    FROM public.platform_access_grant_permission AS view_capability
    JOIN public.platform_access_grant_permission AS manage_capability
      ON manage_capability.grant_id = view_capability.grant_id
     AND manage_capability.permission_code = 'platform.access.manage'
    WHERE view_capability.grant_id = NEW.id
      AND view_capability.permission_code = 'platform.access.view'
  ) THEN
    RAISE EXCEPTION 'Developer grant requires platform access governance'
      USING ERRCODE = '23514';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


AUDIT_GRANT_PERMISSION_SQL = """
CREATE FUNCTION public.trg_audit_platform_access_grant_permission()
RETURNS TRIGGER AS $$
DECLARE
  v_request_id TEXT;
  v_target_user_id UUID;
BEGIN
  SELECT grant_row.user_id
  INTO v_target_user_id
  FROM public.platform_access_grant AS grant_row
  WHERE grant_row.id = NEW.grant_id;

  v_request_id := NULLIF(
    pg_catalog.current_setting('app.request_id', true),
    ''
  );

  INSERT INTO public.audit_log (
    tenant_id,
    user_id,
    action,
    table_name,
    record_id,
    old_values,
    new_values,
    changed_fields,
    metadata,
    created_at
  ) VALUES (
    NULL,
    public.current_app_user_id(),
    'INSERT',
    'platform_access_grant_permission',
    NEW.grant_id,
    NULL,
    pg_catalog.jsonb_build_object('permission_code', NEW.permission_code),
    NULL,
    pg_catalog.jsonb_strip_nulls(
      pg_catalog.jsonb_build_object(
        'event', 'platform_capability_granted',
        'target_user_id', v_target_user_id,
        'permission_code', NEW.permission_code,
        'request_id', v_request_id
      )
    ),
    pg_catalog.statement_timestamp()
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _secure_trigger_function(signature: str) -> None:
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} " "FROM PUBLIC, aurum_app, aurum_support"
    )


def upgrade() -> None:
    op.execute("""
        INSERT INTO public.permission (
          code,
          group_code,
          name,
          description,
          min_level_required,
          is_dangerous,
          is_active,
          scope_type,
          target_role_type,
          risk_level,
          developer_grantable,
          administrator_grantable,
          owner_grantable,
          developer_delegable,
          administrator_delegable,
          owner_delegable,
          requires_step_up,
          requires_confirmation
        ) VALUES
          ('platform.tenants.view', 'platform_tenants', 'Просмотр аптек',
           'Просмотр списка и карточек аптек', 2, false, true, 'PLATFORM',
           'platform', 'sensitive', true, true, false, true, false, false, true, false),
          ('platform.tenants.manage', 'platform_tenants', 'Управление аптеками',
           'Создание и изменение аптек', 2, true, true, 'PLATFORM',
           'platform', 'critical', true, true, false, true, false, false, true, true),
          ('platform.memberships.manage', 'platform_accounts', 'Управление аккаунтами аптек',
           'Создание tenant-аккаунтов и membership', 2, true, true, 'PLATFORM',
           'platform', 'critical', true, true, false, true, false, false, true, true),
          ('platform.ownership.provision', 'platform_accounts', 'Первичное владение аптекой',
           'Создание первого владельца аптеки', 2, true, true, 'PLATFORM',
           'platform', 'critical', true, true, false, true, false, false, true, true),
          ('platform.billing.manage', 'platform_billing', 'Управление биллингом',
           'Подписки, счета, оплаты и billing-статус', 2, true, true, 'PLATFORM',
           'platform', 'critical', true, true, false, true, false, false, true, true),
          ('platform.support.use', 'platform_support', 'Support-доступ',
           'Запуск и отзыв ограниченных support-сессий', 2, true, true, 'PLATFORM',
           'platform', 'critical', true, true, false, true, false, false, true, true),
          ('platform.sync.view', 'platform_sync', 'Просмотр Edge-узлов',
           'Просмотр безопасных метаданных Edge-синхронизации', 2, false, true, 'PLATFORM',
           'platform', 'sensitive', true, true, false, true, false, false, true, false),
          ('platform.sync.manage', 'platform_sync', 'Управление Edge-синхронизацией',
           'Credentials, отзыв узлов и writer handover', 1, true, true, 'PLATFORM',
           'platform', 'critical', true, false, false, true, false, false, true, true),
          ('platform.audit.global.view', 'platform_security', 'Глобальный аудит',
           'Просмотр глобального неизменяемого аудита', 1, false, true, 'PLATFORM',
           'platform', 'critical', true, false, false, true, false, false, true, false),
          ('platform.access.view', 'platform_access', 'Просмотр platform-доступа',
           'Просмотр каталога и истории platform grants', 1, false, true, 'PLATFORM',
           'platform', 'critical', true, false, false, true, false, false, true, false),
          ('platform.access.manage', 'platform_access', 'Управление platform-доступом',
           'Запрос, подтверждение и отзыв platform grants', 1, true, true, 'PLATFORM',
           'platform', 'critical', true, false, false, true, false, false, true, true)
        """)

    op.execute("""
        DO $$
        DECLARE
          v_grant_had_references BOOLEAN := pg_catalog.has_table_privilege(
            'aurum_schema_owner',
            'public.platform_access_grant',
            'REFERENCES'
          );
          v_permission_had_references BOOLEAN := pg_catalog.has_table_privilege(
            'aurum_schema_owner',
            'public.permission',
            'REFERENCES'
          );
          v_user_had_references BOOLEAN := pg_catalog.has_table_privilege(
            'aurum_schema_owner',
            'public.app_user',
            'REFERENCES'
          );
        BEGIN
          IF NOT v_grant_had_references THEN
            GRANT REFERENCES ON TABLE public.platform_access_grant
              TO aurum_schema_owner;
          END IF;
          IF NOT v_permission_had_references THEN
            GRANT REFERENCES ON TABLE public.permission TO aurum_schema_owner;
          END IF;
          IF NOT v_user_had_references THEN
            GRANT REFERENCES ON TABLE public.app_user TO aurum_schema_owner;
          END IF;

          CREATE TABLE public.platform_access_grant_permission (
            grant_id UUID NOT NULL
              REFERENCES public.platform_access_grant(id) ON DELETE RESTRICT,
            permission_code TEXT NOT NULL
              REFERENCES public.permission(code) ON UPDATE RESTRICT ON DELETE RESTRICT,
            created_by UUID
              REFERENCES public.app_user(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
            PRIMARY KEY (grant_id, permission_code)
          );

          IF NOT v_grant_had_references THEN
            REVOKE REFERENCES ON TABLE public.platform_access_grant
              FROM aurum_schema_owner;
          END IF;
          IF NOT v_permission_had_references THEN
            REVOKE REFERENCES ON TABLE public.permission FROM aurum_schema_owner;
          END IF;
          IF NOT v_user_had_references THEN
            REVOKE REFERENCES ON TABLE public.app_user FROM aurum_schema_owner;
          END IF;
        END
        $$
        """)

    op.execute("""
        INSERT INTO public.platform_access_grant_permission (
          grant_id,
          permission_code,
          created_by
        )
        SELECT
          grant_row.id,
          permission.code,
          grant_row.requested_by
        FROM public.platform_access_grant AS grant_row
        JOIN public.permission AS permission
          ON permission.is_active
         AND permission.target_role_type = 'platform'
         AND permission.scope_type = 'PLATFORM'
         AND permission.developer_delegable
         AND (
           (grant_row.access_kind = 'developer' AND permission.developer_grantable)
           OR (
             grant_row.access_kind = 'administrator'
             AND permission.administrator_grantable
           )
         )
        """)

    op.execute(GUARD_GRANT_PERMISSION_SQL)
    _secure_trigger_function("public.trg_guard_platform_access_grant_permission()")
    op.execute(SEED_BOOTSTRAP_CAPABILITIES_SQL)
    _secure_trigger_function("public.trg_seed_bootstrap_platform_capabilities()")
    op.execute(VALIDATE_GRANT_CAPABILITIES_SQL)
    _secure_trigger_function("public.trg_validate_platform_grant_capabilities()")
    op.execute(AUDIT_GRANT_PERMISSION_SQL)
    _secure_trigger_function("public.trg_audit_platform_access_grant_permission()")

    op.execute("""
        DO $$
        DECLARE
          v_grant_had_trigger BOOLEAN := pg_catalog.has_table_privilege(
            'aurum_schema_owner',
            'public.platform_access_grant',
            'TRIGGER'
          );
        BEGIN
          IF NOT v_grant_had_trigger THEN
            GRANT TRIGGER ON TABLE public.platform_access_grant
              TO aurum_schema_owner;
          END IF;

          CREATE TRIGGER trg_10_guard_platform_access_grant_permission
          BEFORE INSERT OR DELETE OR UPDATE ON public.platform_access_grant_permission
          FOR EACH ROW EXECUTE FUNCTION public.trg_guard_platform_access_grant_permission();

          CREATE TRIGGER trg_20_audit_platform_access_grant_permission
          AFTER INSERT ON public.platform_access_grant_permission
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_platform_access_grant_permission();

          CREATE TRIGGER trg_15_seed_bootstrap_platform_capabilities
          AFTER INSERT ON public.platform_access_grant
          FOR EACH ROW EXECUTE FUNCTION public.trg_seed_bootstrap_platform_capabilities();

          CREATE CONSTRAINT TRIGGER trg_90_validate_platform_grant_capabilities
          AFTER INSERT OR UPDATE OF status ON public.platform_access_grant
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION public.trg_validate_platform_grant_capabilities();

          IF NOT v_grant_had_trigger THEN
            REVOKE TRIGGER ON TABLE public.platform_access_grant
              FROM aurum_schema_owner;
          END IF;
        END
        $$
        """)

    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.platform_access_grant_permission "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT, INSERT ON TABLE public.platform_access_grant_permission " "TO aurum_support"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_90_validate_platform_grant_capabilities "
        "ON public.platform_access_grant"
    )
    op.execute(
        "DROP TRIGGER trg_15_seed_bootstrap_platform_capabilities "
        "ON public.platform_access_grant"
    )
    op.execute(
        "DROP TRIGGER trg_20_audit_platform_access_grant_permission "
        "ON public.platform_access_grant_permission"
    )
    op.execute(
        "DROP TRIGGER trg_10_guard_platform_access_grant_permission "
        "ON public.platform_access_grant_permission"
    )
    op.execute("DROP FUNCTION public.trg_audit_platform_access_grant_permission()")
    op.execute("DROP FUNCTION public.trg_validate_platform_grant_capabilities()")
    op.execute("DROP FUNCTION public.trg_seed_bootstrap_platform_capabilities()")
    op.execute("DROP FUNCTION public.trg_guard_platform_access_grant_permission()")
    op.execute("DROP TABLE public.platform_access_grant_permission")
    codes = ", ".join(f"'{code}'" for code in PLATFORM_PERMISSION_CODES)
    op.execute(f"DELETE FROM public.permission WHERE code IN ({codes})")
