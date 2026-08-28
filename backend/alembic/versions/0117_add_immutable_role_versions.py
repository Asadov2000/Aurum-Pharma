"""Add immutable published role versions and pin assignments.

Revision ID: 0117
Revises: 0116
Create Date: 2026-08-28
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0117"
down_revision: str | Sequence[str] | None = "0116"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFERENCE_TABLES = ("role", "tenant", "app_user", "permission")
NEW_AUDIT_ACTIONS = (
    "ROLE_VERSION_PUBLISHED",
    "ROLE_ARCHIVED_WITH_REPLACEMENT",
)


def _grant_missing_reference_privileges() -> None:
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0117_missing_reference_privilege (
          table_name TEXT PRIMARY KEY
        ) ON COMMIT DROP
        """)
    for table_name in REFERENCE_TABLES:
        op.execute(f"""
            DO $$
            BEGIN
              IF NOT pg_catalog.has_table_privilege(
                'aurum_schema_owner',
                'public.{table_name}',
                'REFERENCES'
              ) THEN
                INSERT INTO pg_temp.aurum_0117_missing_reference_privilege (
                  table_name
                ) VALUES ('{table_name}');
                GRANT REFERENCES ON TABLE public.{table_name}
                  TO aurum_schema_owner;
              END IF;
            END
            $$
            """)


def _restore_reference_privileges() -> None:
    for table_name in REFERENCE_TABLES:
        op.execute(f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM pg_temp.aurum_0117_missing_reference_privilege
                WHERE table_name = '{table_name}'
              ) THEN
                REVOKE REFERENCES ON TABLE public.{table_name}
                  FROM aurum_schema_owner;
              END IF;
            END
            $$
            """)
    op.execute("DROP TABLE pg_temp.aurum_0117_missing_reference_privilege")


def _set_audit_actions(actions: Sequence[str]) -> None:
    values = ", ".join(f"'{action}'" for action in actions)
    op.execute("ALTER TABLE public.audit_log DROP CONSTRAINT audit_log_action_check")
    op.execute(
        "ALTER TABLE public.audit_log "
        f"ADD CONSTRAINT audit_log_action_check CHECK (action IN ({values}))"
    )


def _load_revision_module(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"aurum_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration source: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _secure_function(signature: str, *, application_access: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} " "FROM PUBLIC, aurum_app, aurum_support"
    )
    if application_access:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_app, aurum_support")


def _version_aware_role_guard(source_0062: ModuleType) -> str:
    statement = source_0062.ROLE_MUTATION_GUARD_SQL
    marker = "BEGIN\n  IF (public.is_support_session()"
    guard = """
  IF TG_OP = 'UPDATE'
    AND EXISTS (
      SELECT 1
      FROM public.access_role_version AS existing_version
      WHERE existing_version.role_id = OLD.id
    )
    AND NOT EXISTS (
      SELECT 1
      FROM public.access_role_version AS pending_version
      WHERE pending_version.role_id = OLD.id
        AND pending_version.version = NEW.version
        AND pending_version.status = 'draft'
        AND pending_version.creation_xid = pg_catalog.txid_current()
        AND pending_version.created_by = public.current_app_user_id()
    )
  THEN
    RAISE EXCEPTION 'Role updates require a protected publication workflow'
      USING ERRCODE = '42501';
  END IF;

"""
    if marker not in statement:
        raise RuntimeError("Tenant role guard contract changed")
    return statement.replace(marker, "BEGIN\n" + guard + "  IF (public.is_support_session()", 1)


def _version_aware_permission_guard(source_0062: ModuleType) -> str:
    statement = source_0062.ROLE_PERMISSION_MUTATION_GUARD_SQL
    marker = "BEGIN\n  IF (public.is_support_session()"
    guard = """
  IF EXISTS (
    SELECT 1
    FROM public.access_role_version AS existing_version
    WHERE existing_version.role_id = CASE
      WHEN TG_OP = 'DELETE' THEN OLD.role_id
      ELSE NEW.role_id
    END
  ) AND NOT EXISTS (
    SELECT 1
    FROM public.access_role_version AS pending_version
    WHERE pending_version.role_id = CASE
        WHEN TG_OP = 'DELETE' THEN OLD.role_id
        ELSE NEW.role_id
      END
      AND pending_version.status = 'draft'
      AND pending_version.creation_xid = pg_catalog.txid_current()
      AND pending_version.created_by = public.current_app_user_id()
  ) THEN
    RAISE EXCEPTION 'Role permissions require a protected publication workflow'
      USING ERRCODE = '42501';
  END IF;

"""
    if marker not in statement:
        raise RuntimeError("Role permission guard contract changed")
    return statement.replace(marker, "BEGIN\n" + guard + "  IF (public.is_support_session()", 1)


VERSION_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_access_role_version()
RETURNS TRIGGER AS $$
BEGIN
  IF session_user NOT IN ('aurum_app', 'aurum_support') THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Role versions are immutable'
      USING ERRCODE = '42501';
  END IF;

  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft'
      OR NEW.creation_xid <> pg_catalog.txid_current()
      OR NEW.created_by IS DISTINCT FROM public.current_app_user_id()
    THEN
      RAISE EXCEPTION 'Role versions require a protected publication workflow'
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;

  IF OLD.id IS DISTINCT FROM NEW.id
    OR OLD.role_id IS DISTINCT FROM NEW.role_id
    OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
    OR OLD.version IS DISTINCT FROM NEW.version
    OR OLD.name IS DISTINCT FROM NEW.name
    OR OLD.description IS DISTINCT FROM NEW.description
    OR OLD.creation_xid IS DISTINCT FROM NEW.creation_xid
    OR OLD.created_at IS DISTINCT FROM NEW.created_at
    OR OLD.created_by IS DISTINCT FROM NEW.created_by
  THEN
    RAISE EXCEPTION 'Published role version contents are immutable'
      USING ERRCODE = '42501';
  END IF;

  IF OLD.status = 'draft'
    AND OLD.creation_xid = pg_catalog.txid_current()
    AND OLD.created_by = public.current_app_user_id()
    AND NEW.status IN ('published', 'archived')
  THEN
    RETURN NEW;
  END IF;

  IF OLD.status = 'published'
    AND NEW.status = 'archived'
    AND OLD.published_at IS NOT DISTINCT FROM NEW.published_at
    AND OLD.archived_at IS NULL
    AND NEW.archived_at IS NOT NULL
  THEN
    RETURN NEW;
  END IF;

  RAISE EXCEPTION 'Published role versions are immutable'
    USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


VERSION_PERMISSION_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_access_role_version_permission()
RETURNS TRIGGER AS $$
DECLARE
  v_version_id UUID := CASE
    WHEN TG_OP = 'DELETE' THEN OLD.role_version_id
    ELSE NEW.role_version_id
  END;
BEGIN
  IF session_user NOT IN ('aurum_app', 'aurum_support') THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP <> 'INSERT'
    OR NOT EXISTS (
      SELECT 1
      FROM public.access_role_version AS role_version
      WHERE role_version.id = v_version_id
        AND role_version.status = 'draft'
        AND role_version.creation_xid = pg_catalog.txid_current()
        AND role_version.created_by = public.current_app_user_id()
    )
  THEN
    RAISE EXCEPTION 'Published role version permissions are immutable'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ASSIGNMENT_VERSION_GUARD_SQL = """
CREATE FUNCTION public.trg_pin_assignment_role_version()
RETURNS TRIGGER AS $$
DECLARE
  v_version_status TEXT;
BEGIN
  IF TG_OP = 'INSERT'
    OR NEW.role_id IS DISTINCT FROM OLD.role_id
    OR (NOT COALESCE(OLD.is_active, false) AND NEW.is_active)
  THEN
    SELECT role_version.id
    INTO NEW.role_version_id
    FROM public.access_role_version AS role_version
    WHERE role_version.role_id = NEW.role_id
      AND role_version.status = 'published'
    ORDER BY role_version.version DESC
    LIMIT 1;
  END IF;

  IF NEW.role_version_id IS NULL THEN
    RAISE EXCEPTION 'Assignment requires a published role version'
      USING ERRCODE = '23514';
  END IF;

  SELECT role_version.status
  INTO v_version_status
  FROM public.access_role_version AS role_version
  WHERE role_version.id = NEW.role_version_id
    AND role_version.role_id = NEW.role_id
    AND role_version.tenant_id = NEW.tenant_id;

  IF v_version_status IS NULL OR (NEW.is_active AND v_version_status <> 'published') THEN
    RAISE EXCEPTION 'Assignment role version is unavailable'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


INITIALIZE_VERSION_SQL = """
CREATE FUNCTION public.initialize_tenant_role_version(p_role_id UUID)
RETURNS UUID AS $$
DECLARE
  v_actor_id UUID := public.current_app_user_id();
  v_matches_active_template BOOLEAN := false;
  v_role public.role%ROWTYPE;
  v_version_id UUID := pg_catalog.gen_random_uuid();
BEGIN
  SELECT role.* INTO v_role
  FROM public.role AS role
  WHERE role.id = p_role_id
  FOR UPDATE;

  SELECT EXISTS (
    SELECT 1
    FROM public.role_template AS template
    WHERE template.is_active
      AND NOT EXISTS (
        (
          SELECT template_permission.permission_code
          FROM public.role_template_permission AS template_permission
          WHERE template_permission.template_id = template.id
          EXCEPT
          SELECT role_permission.permission_code
          FROM public.role_permission AS role_permission
          WHERE role_permission.role_id = p_role_id
        )
        UNION ALL
        (
          SELECT role_permission.permission_code
          FROM public.role_permission AS role_permission
          WHERE role_permission.role_id = p_role_id
          EXCEPT
          SELECT template_permission.permission_code
          FROM public.role_template_permission AS template_permission
          WHERE template_permission.template_id = template.id
        )
      )
  ) INTO v_matches_active_template;

  IF v_actor_id IS NULL
    OR v_role.id IS NULL
    OR v_role.is_system
    OR NOT v_role.is_active
    OR v_role.version <> 1
    OR EXISTS (
      SELECT 1 FROM public.access_role_version WHERE role_id = p_role_id
    )
    OR NOT public.ownership_transfer_mfa_is_recent()
    OR (
      v_role.is_protected
      AND (
        v_role.protected_kind <> 'tenant_owner'
        OR session_user <> 'aurum_support'
        OR NOT public.is_support_session()
      )
    )
    OR (
      NOT v_role.is_protected
      AND NOT (
        (
          session_user = 'aurum_support'
          AND public.current_tenant_id() IS NULL
          AND public.is_support_session()
          AND v_matches_active_template
        )
        OR (
          v_role.tenant_id IS NOT DISTINCT FROM public.current_tenant_id()
          AND public.tenant_actor_has_permission(v_role.tenant_id, 'roles.create')
          AND (
            public.is_tenant_support_session()
            OR public.tenant_actor_is_owner(v_role.tenant_id)
          )
        )
      )
    )
  THEN
    RAISE EXCEPTION 'Initial role publication is unavailable'
      USING ERRCODE = '42501';
  END IF;

  IF (
    v_role.is_protected
    AND (
      NOT EXISTS (
        SELECT 1 FROM public.role_template AS template
        WHERE template.slug = 'owner' AND template.is_active
      )
      OR EXISTS (
        (
          SELECT role_permission.permission_code
          FROM public.role_permission AS role_permission
          WHERE role_permission.role_id = p_role_id
          EXCEPT
          SELECT template_permission.permission_code
          FROM public.role_template AS template
          JOIN public.role_template_permission AS template_permission
            ON template_permission.template_id = template.id
          WHERE template.slug = 'owner' AND template.is_active
        )
        UNION ALL
        (
          SELECT template_permission.permission_code
          FROM public.role_template AS template
          JOIN public.role_template_permission AS template_permission
            ON template_permission.template_id = template.id
          WHERE template.slug = 'owner' AND template.is_active
          EXCEPT
          SELECT role_permission.permission_code
          FROM public.role_permission AS role_permission
          WHERE role_permission.role_id = p_role_id
        )
      )
    )
  ) OR (
    NOT v_role.is_protected
    AND NOT (
      session_user = 'aurum_support'
      AND public.current_tenant_id() IS NULL
      AND public.is_support_session()
      AND v_matches_active_template
    )
    AND EXISTS (
      SELECT 1
      FROM public.role_permission AS role_permission
      JOIN public.permission AS permission
        ON permission.code = role_permission.permission_code
      WHERE role_permission.role_id = p_role_id
        AND (
          NOT permission.is_active
          OR permission.target_role_type <> 'tenant'
          OR (
            public.is_tenant_support_session()
            AND NOT public.support_actor_can_delegate_permission(permission.code)
          )
          OR (
            NOT public.is_tenant_support_session()
            AND (
              NOT permission.owner_delegable
              OR NOT public.tenant_actor_has_permission(v_role.tenant_id, permission.code)
            )
          )
        )
    )
  ) THEN
    RAISE EXCEPTION 'Initial role permissions exceed delegation scope'
      USING ERRCODE = '42501';
  END IF;

  INSERT INTO public.access_role_version (
    id, role_id, tenant_id, version, name, description, status,
    creation_xid, created_by
  ) VALUES (
    v_version_id, v_role.id, v_role.tenant_id, 1, v_role.name,
    v_role.description, 'draft', pg_catalog.txid_current(), v_actor_id
  );

  INSERT INTO public.access_role_version_permission (role_version_id, permission_code)
  SELECT v_version_id, role_permission.permission_code
  FROM public.role_permission AS role_permission
  WHERE role_permission.role_id = v_role.id;

  UPDATE public.access_role_version
  SET status = 'published', published_at = pg_catalog.statement_timestamp()
  WHERE id = v_version_id;

  RETURN v_version_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


PUBLISH_VERSION_SQL = """
CREATE FUNCTION public.publish_tenant_role_version(
  p_role_id UUID,
  p_expected_version INTEGER,
  p_name TEXT,
  p_description TEXT,
  p_permissions TEXT[]
) RETURNS TABLE (
  role_version_id UUID,
  published_version INTEGER,
  affected_memberships INTEGER
) AS $$
DECLARE
  v_actor_id UUID := public.current_app_user_id();
  v_affected INTEGER;
  v_current_version_id UUID;
  v_new_version INTEGER;
  v_new_version_id UUID := pg_catalog.gen_random_uuid();
  v_permissions TEXT[];
  v_role public.role%ROWTYPE;
BEGIN
  v_permissions := ARRAY(
    SELECT DISTINCT permission_code
    FROM pg_catalog.unnest(COALESCE(p_permissions, ARRAY[]::TEXT[])) AS permission_code
    WHERE permission_code IS NOT NULL
    ORDER BY permission_code
  );

  SELECT role.* INTO v_role
  FROM public.role AS role
  WHERE role.id = p_role_id
  FOR UPDATE;

  SELECT version.id INTO v_current_version_id
  FROM public.access_role_version AS version
  WHERE version.role_id = p_role_id
    AND version.status = 'published'
  FOR UPDATE;

  IF v_actor_id IS NULL
    OR v_role.id IS NULL
    OR v_role.tenant_id IS DISTINCT FROM public.current_tenant_id()
    OR v_role.is_system
    OR v_role.is_protected
    OR NOT v_role.is_active
    OR v_role.version <> p_expected_version
    OR v_current_version_id IS NULL
    OR NULLIF(pg_catalog.btrim(p_name), '') IS NULL
    OR pg_catalog.char_length(pg_catalog.btrim(p_name)) > 100
    OR pg_catalog.char_length(COALESCE(p_description, '')) > 500
    OR NOT public.ownership_transfer_mfa_is_recent()
    OR NOT public.tenant_actor_has_permission(v_role.tenant_id, 'roles.update')
    OR (
      NOT public.is_tenant_support_session()
      AND NOT public.tenant_actor_is_owner(v_role.tenant_id)
    )
    OR EXISTS (
      SELECT 1 FROM public.user_assignment AS assignment
      WHERE assignment.tenant_id = v_role.tenant_id
        AND assignment.user_id = v_actor_id
        AND assignment.role_id = v_role.id
        AND assignment.is_active
    )
  THEN
    RAISE EXCEPTION 'Role publication preconditions changed'
      USING ERRCODE = '40001';
  END IF;

  IF pg_catalog.cardinality(v_permissions) <> pg_catalog.cardinality(
    COALESCE(p_permissions, ARRAY[]::TEXT[])
  ) OR EXISTS (
    SELECT 1
    FROM pg_catalog.unnest(v_permissions) AS requested(code)
    LEFT JOIN public.permission AS permission ON permission.code = requested.code
    WHERE permission.code IS NULL
      OR NOT permission.is_active
      OR permission.target_role_type <> 'tenant'
      OR (
        public.is_tenant_support_session()
        AND NOT public.support_actor_can_delegate_permission(permission.code)
      )
      OR (
        NOT public.is_tenant_support_session()
        AND (
          NOT permission.owner_delegable
          OR NOT public.tenant_actor_has_permission(v_role.tenant_id, permission.code)
        )
      )
  ) THEN
    RAISE EXCEPTION 'Role publication permissions exceed delegation scope'
      USING ERRCODE = '42501';
  END IF;

  v_new_version := v_role.version + 1;
  SELECT pg_catalog.count(DISTINCT assignment.membership_id)::INTEGER
  INTO v_affected
  FROM public.user_assignment AS assignment
  WHERE assignment.tenant_id = v_role.tenant_id
    AND assignment.role_id = v_role.id
    AND assignment.is_active;

  INSERT INTO public.access_role_version (
    id, role_id, tenant_id, version, name, description, status,
    creation_xid, created_by
  ) VALUES (
    v_new_version_id, v_role.id, v_role.tenant_id, v_new_version,
    pg_catalog.btrim(p_name), NULLIF(pg_catalog.btrim(p_description), ''),
    'draft', pg_catalog.txid_current(), v_actor_id
  );

  INSERT INTO public.access_role_version_permission (role_version_id, permission_code)
  SELECT v_new_version_id, requested.code
  FROM pg_catalog.unnest(v_permissions) AS requested(code);

  UPDATE public.role
  SET
    name = pg_catalog.btrim(p_name),
    description = NULLIF(pg_catalog.btrim(p_description), ''),
    version = v_new_version,
    updated_by = v_actor_id
  WHERE id = v_role.id;

  DELETE FROM public.role_permission WHERE role_id = v_role.id;
  INSERT INTO public.role_permission (role_id, permission_code)
  SELECT v_role.id, requested.code
  FROM pg_catalog.unnest(v_permissions) AS requested(code);

  PERFORM public.record_role_permission_change(
    v_role.id,
    ARRAY(
      SELECT permission_code
      FROM public.access_role_version_permission AS version_permission
      WHERE version_permission.role_version_id = v_current_version_id
      ORDER BY permission_code
    ),
    v_permissions
  );

  UPDATE public.access_role_version
  SET status = 'archived', archived_at = pg_catalog.statement_timestamp()
  WHERE id = v_current_version_id;

  UPDATE public.access_role_version
  SET status = 'published', published_at = pg_catalog.statement_timestamp()
  WHERE id = v_new_version_id;

  UPDATE public.user_assignment
  SET role_version_id = v_new_version_id, updated_by = v_actor_id
  WHERE tenant_id = v_role.tenant_id
    AND role_id = v_role.id
    AND is_active;

  INSERT INTO public.audit_log (
    tenant_id, user_id, action, table_name, record_id, metadata, created_at
  ) VALUES (
    v_role.tenant_id,
    v_actor_id,
    'ROLE_VERSION_PUBLISHED',
    'access_role_version',
    v_new_version_id,
    pg_catalog.jsonb_build_object(
      'role_id', v_role.id,
      'previous_version', v_role.version,
      'published_version', v_new_version,
      'affected_memberships', v_affected
    ),
    pg_catalog.statement_timestamp()
  );

  RETURN QUERY SELECT v_new_version_id, v_new_version, v_affected;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ARCHIVE_ROLE_SQL = """
CREATE FUNCTION public.archive_tenant_role_with_replacement(
  p_role_id UUID,
  p_expected_version INTEGER,
  p_replacement_role_id UUID
) RETURNS TABLE (
  archived_version INTEGER,
  affected_memberships INTEGER
) AS $$
DECLARE
  v_actor_id UUID := public.current_app_user_id();
  v_affected INTEGER;
  v_archive_version_id UUID := pg_catalog.gen_random_uuid();
  v_current_version_id UUID;
  v_new_version INTEGER;
  v_replacement_version_id UUID;
  v_role public.role%ROWTYPE;
BEGIN
  SELECT role.* INTO v_role
  FROM public.role AS role
  WHERE role.id = p_role_id
  FOR UPDATE;

  SELECT version.id INTO v_current_version_id
  FROM public.access_role_version AS version
  WHERE version.role_id = p_role_id AND version.status = 'published'
  FOR UPDATE;

  SELECT version.id INTO v_replacement_version_id
  FROM public.role AS replacement
  JOIN public.access_role_version AS version
    ON version.role_id = replacement.id AND version.status = 'published'
  WHERE replacement.id = p_replacement_role_id
    AND replacement.tenant_id = v_role.tenant_id
    AND replacement.is_active
    AND NOT replacement.is_system
    AND NOT replacement.is_protected
  FOR UPDATE OF replacement, version;

  IF v_actor_id IS NULL
    OR v_role.id IS NULL
    OR p_replacement_role_id = p_role_id
    OR v_role.tenant_id IS DISTINCT FROM public.current_tenant_id()
    OR v_role.is_system
    OR v_role.is_protected
    OR NOT v_role.is_active
    OR v_role.version <> p_expected_version
    OR v_current_version_id IS NULL
    OR v_replacement_version_id IS NULL
    OR NOT public.ownership_transfer_mfa_is_recent()
    OR NOT public.tenant_actor_has_permission(v_role.tenant_id, 'roles.update')
    OR NOT public.tenant_actor_has_permission(v_role.tenant_id, 'roles.assign')
    OR (
      NOT public.is_tenant_support_session()
      AND NOT public.tenant_actor_is_owner(v_role.tenant_id)
    )
    OR EXISTS (
      SELECT 1 FROM public.user_assignment AS assignment
      WHERE assignment.tenant_id = v_role.tenant_id
        AND assignment.user_id = v_actor_id
        AND assignment.role_id = v_role.id
        AND assignment.is_active
    )
  THEN
    RAISE EXCEPTION 'Role archive preconditions changed'
      USING ERRCODE = '40001';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.access_role_version_permission AS version_permission
    LEFT JOIN public.permission AS permission
      ON permission.code = version_permission.permission_code
    WHERE version_permission.role_version_id IN (
        v_current_version_id,
        v_replacement_version_id
      )
      AND (
        permission.code IS NULL
        OR NOT permission.is_active
        OR permission.target_role_type <> 'tenant'
        OR (
          public.is_tenant_support_session()
          AND NOT public.support_actor_can_delegate_permission(permission.code)
        )
        OR (
          NOT public.is_tenant_support_session()
          AND (
            NOT permission.owner_delegable
            OR NOT public.tenant_actor_has_permission(v_role.tenant_id, permission.code)
          )
        )
      )
  ) THEN
    RAISE EXCEPTION 'Role archive exceeds delegation scope'
      USING ERRCODE = '42501';
  END IF;

  v_new_version := v_role.version + 1;
  SELECT pg_catalog.count(DISTINCT assignment.membership_id)::INTEGER
  INTO v_affected
  FROM public.user_assignment AS assignment
  WHERE assignment.tenant_id = v_role.tenant_id
    AND assignment.role_id = v_role.id
    AND assignment.is_active;

  INSERT INTO public.access_role_version (
    id, role_id, tenant_id, version, name, description, status,
    creation_xid, published_at, archived_at, created_by
  ) VALUES (
    v_archive_version_id, v_role.id, v_role.tenant_id, v_new_version,
    v_role.name, v_role.description, 'draft', pg_catalog.txid_current(),
    NULL, NULL, v_actor_id
  );
  INSERT INTO public.access_role_version_permission (role_version_id, permission_code)
  SELECT v_archive_version_id, permission.permission_code
  FROM public.access_role_version_permission AS permission
  WHERE permission.role_version_id = v_current_version_id;

  UPDATE public.user_assignment
  SET
    role_id = p_replacement_role_id,
    role_version_id = v_replacement_version_id,
    updated_by = v_actor_id
  WHERE tenant_id = v_role.tenant_id
    AND role_id = v_role.id
    AND is_active;

  UPDATE public.role
  SET is_active = false, version = v_new_version, updated_by = v_actor_id
  WHERE id = v_role.id;

  UPDATE public.access_role_version
  SET status = 'archived', archived_at = pg_catalog.statement_timestamp()
  WHERE id = v_current_version_id;

  UPDATE public.access_role_version
  SET
    status = 'archived',
    published_at = pg_catalog.statement_timestamp(),
    archived_at = pg_catalog.statement_timestamp()
  WHERE id = v_archive_version_id;

  INSERT INTO public.audit_log (
    tenant_id, user_id, action, table_name, record_id, metadata, created_at
  ) VALUES (
    v_role.tenant_id,
    v_actor_id,
    'ROLE_ARCHIVED_WITH_REPLACEMENT',
    'role',
    v_role.id,
    pg_catalog.jsonb_build_object(
      'role_id', v_role.id,
      'replacement_role_id', p_replacement_role_id,
      'archived_version', v_new_version,
      'affected_memberships', v_affected
    ),
    pg_catalog.statement_timestamp()
  );

  RETURN QUERY SELECT v_new_version, v_affected;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def upgrade() -> None:
    source_0053 = _load_revision_module("0053_add_scoped_delegated_authorization.py")
    source_0062 = _load_revision_module("0062_add_scoped_support_access_sessions.py")

    _grant_missing_reference_privileges()
    op.execute("""
        CREATE TABLE public.access_role_version (
          id UUID PRIMARY KEY,
          role_id UUID NOT NULL REFERENCES public.role(id) ON DELETE RESTRICT,
          tenant_id UUID REFERENCES public.tenant(id) ON DELETE RESTRICT,
          version INTEGER NOT NULL,
          name TEXT NOT NULL,
          description TEXT,
          status TEXT NOT NULL,
          creation_xid BIGINT NOT NULL,
          published_at TIMESTAMPTZ,
          archived_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          created_by UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
          CONSTRAINT uq_access_role_version_role_version UNIQUE (role_id, version),
          CONSTRAINT uq_access_role_version_id_role UNIQUE (id, role_id),
          CONSTRAINT ck_access_role_version_number CHECK (version >= 1),
          CONSTRAINT ck_access_role_version_name CHECK (
            char_length(btrim(name)) BETWEEN 1 AND 100
          ),
          CONSTRAINT ck_access_role_version_description CHECK (
            description IS NULL OR char_length(description) <= 500
          ),
          CONSTRAINT ck_access_role_version_status CHECK (
            status IN ('draft', 'published', 'archived')
          ),
          CONSTRAINT ck_access_role_version_lifecycle CHECK (
            (status = 'draft' AND published_at IS NULL AND archived_at IS NULL)
            OR (status = 'published' AND published_at IS NOT NULL AND archived_at IS NULL)
            OR (status = 'archived' AND published_at IS NOT NULL AND archived_at IS NOT NULL)
          )
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_access_role_version_published
        ON public.access_role_version (role_id)
        WHERE status = 'published'
        """)
    op.execute("""
        CREATE INDEX ix_access_role_version_tenant_role
        ON public.access_role_version (tenant_id, role_id, version DESC)
        """)
    op.execute("""
        CREATE TABLE public.access_role_version_permission (
          role_version_id UUID NOT NULL
            REFERENCES public.access_role_version(id) ON DELETE RESTRICT,
          permission_code TEXT NOT NULL
            REFERENCES public.permission(code) ON DELETE RESTRICT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (role_version_id, permission_code)
        )
        """)
    _restore_reference_privileges()

    op.execute("""
        INSERT INTO public.access_role_version (
          id, role_id, tenant_id, version, name, description, status,
          creation_xid, published_at, archived_at, created_by
        )
        SELECT
          gen_random_uuid(), role.id, role.tenant_id, role.version, role.name,
          role.description,
          CASE WHEN role.is_active THEN 'published' ELSE 'archived' END,
          txid_current(), statement_timestamp(),
          CASE WHEN role.is_active THEN NULL ELSE statement_timestamp() END,
          role.created_by
        FROM public.role AS role
        """)
    op.execute("""
        INSERT INTO public.access_role_version_permission (
          role_version_id, permission_code, created_at
        )
        SELECT version.id, role_permission.permission_code, role_permission.created_at
        FROM public.role_permission AS role_permission
        JOIN public.access_role_version AS version
          ON version.role_id = role_permission.role_id
         AND version.status = 'published'
        """)

    op.execute("ALTER TABLE public.user_assignment ADD COLUMN role_version_id UUID")
    op.execute("ALTER TABLE public.user_assignment DISABLE TRIGGER USER")
    op.execute("""
        UPDATE public.user_assignment AS assignment
        SET role_version_id = version.id
        FROM public.access_role_version AS version
        WHERE version.role_id = assignment.role_id
        """)
    op.execute("ALTER TABLE public.user_assignment ENABLE TRIGGER USER")
    op.execute("ALTER TABLE public.user_assignment ALTER COLUMN role_version_id SET NOT NULL")
    op.execute("""
        ALTER TABLE public.user_assignment
          ADD CONSTRAINT fk_user_assignment_role_version_role
          FOREIGN KEY (role_version_id, role_id)
          REFERENCES public.access_role_version(id, role_id)
          ON DELETE RESTRICT
        """)
    op.execute(
        "CREATE INDEX ix_user_assignment_role_version "
        "ON public.user_assignment (role_version_id)"
    )

    for table in ("access_role_version", "access_role_version_permission"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE public.{table} "
            "FROM PUBLIC, aurum_app, aurum_support"
        )
        op.execute(f"GRANT SELECT ON TABLE public.{table} TO aurum_app, aurum_support")

    op.execute("""
        CREATE POLICY access_role_version_schema_owner
        ON public.access_role_version TO aurum_schema_owner
        USING (true) WITH CHECK (true)
        """)
    op.execute("""
        CREATE POLICY access_role_version_tenant_read
        ON public.access_role_version FOR SELECT TO aurum_app, aurum_support
        USING (tenant_id = public.current_tenant_id())
        """)
    op.execute("""
        CREATE POLICY access_role_version_permission_schema_owner
        ON public.access_role_version_permission TO aurum_schema_owner
        USING (true) WITH CHECK (true)
        """)
    op.execute("""
        CREATE POLICY access_role_version_permission_tenant_read
        ON public.access_role_version_permission FOR SELECT TO aurum_app, aurum_support
        USING (
          EXISTS (
            SELECT 1 FROM public.access_role_version AS version
            WHERE version.id = role_version_id
          )
        )
        """)

    op.execute(VERSION_GUARD_SQL)
    _secure_function("public.trg_guard_access_role_version()")
    op.execute(VERSION_PERMISSION_GUARD_SQL)
    _secure_function("public.trg_guard_access_role_version_permission()")
    op.execute(ASSIGNMENT_VERSION_GUARD_SQL)
    _secure_function("public.trg_pin_assignment_role_version()")
    op.execute("""
        CREATE TRIGGER trg_guard_access_role_version
        BEFORE INSERT OR DELETE OR UPDATE ON public.access_role_version
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_access_role_version()
        """)
    op.execute("""
        CREATE TRIGGER trg_guard_access_role_version_permission
        BEFORE INSERT OR DELETE OR UPDATE ON public.access_role_version_permission
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_access_role_version_permission()
        """)
    op.execute("""
        CREATE TRIGGER trg_00_pin_assignment_role_version
        BEFORE INSERT OR UPDATE OF role_id, role_version_id, is_active
        ON public.user_assignment
        FOR EACH ROW EXECUTE FUNCTION public.trg_pin_assignment_role_version()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_access_role_version
        AFTER INSERT OR UPDATE ON public.access_role_version
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)

    op.execute(_version_aware_role_guard(source_0062))
    op.execute(_version_aware_permission_guard(source_0062))

    op.execute(INITIALIZE_VERSION_SQL)
    _secure_function("public.initialize_tenant_role_version(UUID)", application_access=True)
    op.execute(PUBLISH_VERSION_SQL)
    _secure_function(
        "public.publish_tenant_role_version(UUID, INTEGER, TEXT, TEXT, TEXT[])",
        application_access=True,
    )
    op.execute(ARCHIVE_ROLE_SQL)
    _secure_function(
        "public.archive_tenant_role_with_replacement(UUID, INTEGER, UUID)",
        application_access=True,
    )
    _set_audit_actions((*source_0053.AUDIT_ACTIONS, *NEW_AUDIT_ACTIONS))


def downgrade() -> None:
    source_0062 = _load_revision_module("0062_add_scoped_support_access_sessions.py")

    # Append-only audit rows may already use the 0117 actions. Keep those values
    # valid while removing the role-version schema so downgrade remains usable.
    op.execute("DROP FUNCTION public.archive_tenant_role_with_replacement(UUID, INTEGER, UUID)")
    op.execute(
        "DROP FUNCTION public.publish_tenant_role_version(UUID, INTEGER, TEXT, TEXT, TEXT[])"
    )
    op.execute("DROP FUNCTION public.initialize_tenant_role_version(UUID)")
    op.execute(source_0062.ROLE_PERMISSION_MUTATION_GUARD_SQL)
    op.execute(source_0062.ROLE_MUTATION_GUARD_SQL)

    op.execute("DROP TRIGGER trg_00_pin_assignment_role_version ON public.user_assignment")
    op.execute("DROP FUNCTION public.trg_pin_assignment_role_version()")
    op.execute(
        "ALTER TABLE public.user_assignment " "DROP CONSTRAINT fk_user_assignment_role_version_role"
    )
    op.execute("DROP INDEX public.ix_user_assignment_role_version")
    op.execute("ALTER TABLE public.user_assignment DROP COLUMN role_version_id")

    op.execute(
        "DROP TRIGGER trg_guard_access_role_version_permission "
        "ON public.access_role_version_permission"
    )
    op.execute("DROP FUNCTION public.trg_guard_access_role_version_permission()")
    op.execute("DROP TRIGGER trg_audit_access_role_version ON public.access_role_version")
    op.execute("DROP TRIGGER trg_guard_access_role_version ON public.access_role_version")
    op.execute("DROP FUNCTION public.trg_guard_access_role_version()")
    op.execute("DROP TABLE public.access_role_version_permission")
    op.execute("DROP TABLE public.access_role_version")
