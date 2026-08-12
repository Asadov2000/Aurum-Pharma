"""add safe sync node credential rotation and revocation

Revision ID: 0092
Revises: 0091
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0092"
down_revision: str | None = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


AUTHENTICATE_EDGE_NODE_SQL = r"""
CREATE OR REPLACE FUNCTION public.authenticate_edge_node(
  p_credential_kid UUID,
  p_credential_hash TEXT
) RETURNS TABLE(
  node_id UUID,
  tenant_id UUID,
  branch_id UUID,
  credential_issued_at TIMESTAMPTZ,
  credential_expires_at TIMESTAMPTZ,
  shadow_start_origin_node_id UUID,
  shadow_start_writer_epoch BIGINT,
  shadow_root_source_checksum TEXT,
  shadow_root_projection_checksum TEXT,
  shadow_start_sequence BIGINT,
  shadow_start_checksum TEXT,
  shadow_start_projection_checksum TEXT
) AS $$
DECLARE
  v_node_id UUID;
  v_rotation_id UUID;
  v_authenticated INTEGER;
  v_now TIMESTAMPTZ := pg_catalog.statement_timestamp();
BEGIN
  IF p_credential_kid IS NULL OR p_credential_hash !~ '^[0-9a-f]{64}$' THEN
    RETURN;
  END IF;

  SELECT credential.node_id, credential.rotation_id
  INTO v_node_id, v_rotation_id
  FROM (
    SELECT sync_node.id AS node_id, NULL::UUID AS rotation_id
    FROM public.sync_node AS sync_node
    WHERE sync_node.node_kind = 'edge'
      AND sync_node.status = 'active'
      AND sync_node.credential_kid = p_credential_kid
      AND sync_node.credential_hash = p_credential_hash
      AND sync_node.credential_issued_at IS NOT NULL
      AND sync_node.credential_expires_at > v_now

    UNION ALL

    SELECT rotation.node_id, rotation.id
    FROM public.sync_node_credential_rotation AS rotation
    JOIN public.sync_node AS sync_node ON sync_node.id = rotation.node_id
    WHERE sync_node.node_kind = 'edge'
      AND sync_node.status = 'active'
      AND rotation.status IN ('pending', 'verified')
      AND rotation.activate_before > v_now
      AND rotation.credential_expires_at > v_now
      AND rotation.credential_kid = p_credential_kid
      AND rotation.credential_hash = p_credential_hash
  ) AS credential
  LIMIT 1;

  IF v_node_id IS NULL THEN
    RETURN;
  END IF;

  IF v_rotation_id IS NOT NULL THEN
    UPDATE public.sync_node_credential_rotation AS rotation
    SET status = 'verified',
        verified_at = COALESCE(rotation.verified_at, v_now),
        updated_at = v_now
    WHERE rotation.id = v_rotation_id
      AND rotation.status = 'pending';

    IF FOUND THEN
      INSERT INTO public.sync_node_admin_event (
        tenant_id, branch_id, node_id, actor_user_id, operation_id,
        event_type, node_version, reason_code, reason, created_at
      )
      SELECT
        sync_node.tenant_id, sync_node.branch_id, sync_node.id, NULL,
        gen_random_uuid(), 'credential_rotation_verified',
        sync_node.lifecycle_version, NULL, NULL, v_now
      FROM public.sync_node AS sync_node
      WHERE sync_node.id = v_node_id;
    END IF;
  END IF;

  RETURN QUERY
  SELECT
    sync_node.id,
    sync_node.tenant_id,
    sync_node.branch_id,
    COALESCE(rotation.credential_issued_at, sync_node.credential_issued_at),
    COALESCE(rotation.credential_expires_at, sync_node.credential_expires_at),
    sync_node.shadow_start_origin_node_id,
    sync_node.shadow_start_writer_epoch,
    writer_epoch.root_source_checksum,
    writer_epoch.root_projection_checksum,
    sync_node.shadow_start_sequence,
    sync_node.shadow_start_checksum,
    sync_node.shadow_start_projection_checksum
  FROM public.sync_node AS sync_node
  JOIN public.sync_writer_epoch AS writer_epoch
    ON writer_epoch.tenant_id = sync_node.tenant_id
   AND writer_epoch.branch_id = sync_node.branch_id
   AND writer_epoch.writer_epoch = sync_node.shadow_start_writer_epoch
   AND writer_epoch.writer_node_id = sync_node.shadow_start_origin_node_id
  LEFT JOIN public.sync_node_credential_rotation AS rotation
    ON rotation.id = v_rotation_id
  WHERE sync_node.id = v_node_id
    AND sync_node.mode IN ('shadow_readonly', 'edge_writer')
    AND sync_node.status = 'active'
    AND (
      (
        sync_node.credential_kid = p_credential_kid
        AND sync_node.credential_hash = p_credential_hash
        AND sync_node.credential_expires_at > v_now
      )
      OR (
        rotation.status IN ('pending', 'verified')
        AND rotation.activate_before > v_now
        AND rotation.credential_expires_at > v_now
        AND rotation.credential_kid = p_credential_kid
        AND rotation.credential_hash = p_credential_hash
      )
    );

  GET DIAGNOSTICS v_authenticated = ROW_COUNT;

  UPDATE public.sync_node
  SET last_seen_at = v_now
  WHERE id = v_node_id AND v_authenticated > 0;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


AUTHENTICATE_EDGE_NODE_DOWN_SQL = r"""
CREATE OR REPLACE FUNCTION public.authenticate_edge_node(
  p_credential_kid UUID,
  p_credential_hash TEXT
) RETURNS TABLE(
  node_id UUID,
  tenant_id UUID,
  branch_id UUID,
  credential_issued_at TIMESTAMPTZ,
  credential_expires_at TIMESTAMPTZ,
  shadow_start_origin_node_id UUID,
  shadow_start_writer_epoch BIGINT,
  shadow_root_source_checksum TEXT,
  shadow_root_projection_checksum TEXT,
  shadow_start_sequence BIGINT,
  shadow_start_checksum TEXT,
  shadow_start_projection_checksum TEXT
) AS $$
BEGIN
  IF p_credential_kid IS NULL OR p_credential_hash !~ '^[0-9a-f]{64}$' THEN
    RETURN;
  END IF;

  RETURN QUERY
  SELECT
    sync_node.id,
    sync_node.tenant_id,
    sync_node.branch_id,
    sync_node.credential_issued_at,
    sync_node.credential_expires_at,
    sync_node.shadow_start_origin_node_id,
    sync_node.shadow_start_writer_epoch,
    writer_epoch.root_source_checksum,
    writer_epoch.root_projection_checksum,
    sync_node.shadow_start_sequence,
    sync_node.shadow_start_checksum,
    sync_node.shadow_start_projection_checksum
  FROM public.sync_node AS sync_node
  JOIN public.sync_writer_epoch AS writer_epoch
    ON writer_epoch.tenant_id = sync_node.tenant_id
   AND writer_epoch.branch_id = sync_node.branch_id
   AND writer_epoch.writer_epoch = sync_node.shadow_start_writer_epoch
   AND writer_epoch.writer_node_id = sync_node.shadow_start_origin_node_id
  WHERE sync_node.node_kind = 'edge'
    AND sync_node.mode IN ('shadow_readonly', 'edge_writer')
    AND sync_node.status = 'active'
    AND sync_node.credential_kid = p_credential_kid
    AND sync_node.credential_hash = p_credential_hash
    AND sync_node.credential_issued_at IS NOT NULL
    AND sync_node.credential_expires_at > pg_catalog.now();

  UPDATE public.sync_node AS sync_node
  SET last_seen_at = pg_catalog.now()
  WHERE sync_node.node_kind = 'edge'
    AND sync_node.mode IN ('shadow_readonly', 'edge_writer')
    AND sync_node.status = 'active'
    AND sync_node.credential_kid = p_credential_kid
    AND sync_node.credential_hash = p_credential_hash
    AND sync_node.credential_issued_at IS NOT NULL
    AND sync_node.credential_expires_at > pg_catalog.now();
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


PREPARE_ROTATION_SQL = r"""
CREATE FUNCTION public.prepare_sync_node_credential_rotation(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_node_id UUID,
  p_expected_version INTEGER,
  p_operation_id UUID,
  p_credential_kid UUID,
  p_credential_hash TEXT,
  p_credential_expires_at TIMESTAMPTZ,
  p_confirmation_name TEXT,
  p_request_hash TEXT,
  p_reason_code TEXT,
  p_reason TEXT
) RETURNS TABLE(
  rotation_id UUID,
  node_id UUID,
  rotation_status TEXT,
  node_version INTEGER,
  credential_issued_at TIMESTAMPTZ,
  credential_expires_at TIMESTAMPTZ,
  activate_before TIMESTAMPTZ,
  verified_at TIMESTAMPTZ,
  applied BOOLEAN
) AS $$
DECLARE
  v_now TIMESTAMPTZ := pg_catalog.statement_timestamp();
  v_node public.sync_node%ROWTYPE;
BEGIN
  IF NOT public.platform_actor_has_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.sync.manage'
  ) THEN
    RAISE EXCEPTION 'Recent sync management capability required'
      USING ERRCODE = '42501';
  END IF;

  IF p_expected_version < 1
    OR p_operation_id IS NULL
    OR p_credential_kid IS NULL
    OR p_credential_hash !~ '^[0-9a-f]{64}$'
    OR p_credential_expires_at <= v_now
    OR p_credential_expires_at > v_now + INTERVAL '365 days 5 minutes'
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_reason_code NOT IN (
      'routine_maintenance', 'credential_expiry', 'security_incident',
      'device_replacement', 'device_retired', 'other'
    )
    OR pg_catalog.char_length(pg_catalog.btrim(p_reason)) NOT BETWEEN 10 AND 500
  THEN
    RAISE EXCEPTION 'Invalid credential rotation request' USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 0)
  );

  IF EXISTS (
    SELECT 1 FROM public.sync_node_admin_event AS event
    WHERE event.operation_id = p_operation_id
      AND event.node_id = p_node_id
      AND event.event_type = 'credential_rotation_started'
      AND event.request_hash = p_request_hash
  ) THEN
    RETURN QUERY
    SELECT
      rotation.id, rotation.node_id, 'pending'::TEXT,
      event.node_version, rotation.credential_issued_at,
      rotation.credential_expires_at, rotation.activate_before,
      rotation.verified_at, false
    FROM public.sync_node_credential_rotation AS rotation
    JOIN public.sync_node_admin_event AS event
      ON event.operation_id = p_operation_id
     AND event.node_id = rotation.node_id
     AND event.event_type = 'credential_rotation_started'
     AND event.request_hash = p_request_hash
    WHERE rotation.id = p_operation_id;
    RETURN;
  ELSIF EXISTS (
    SELECT 1 FROM public.sync_node_admin_event AS event
    WHERE event.operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Operation identifier is already used' USING ERRCODE = '23505';
  END IF;

  SELECT * INTO v_node
  FROM public.sync_node
  WHERE id = p_node_id AND node_kind = 'edge'
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Edge node not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_node.lifecycle_version <> p_expected_version THEN
    RETURN;
  END IF;
  IF v_node.display_name IS DISTINCT FROM p_confirmation_name THEN
    RAISE EXCEPTION 'Edge node confirmation does not match' USING ERRCODE = '55000';
  END IF;
  IF v_node.status <> 'active' THEN
    RAISE EXCEPTION 'Revoked Edge node cannot rotate credentials' USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.sync_node_credential_rotation AS rotation
    WHERE rotation.node_id = p_node_id
      AND rotation.status IN ('pending', 'verified')
  ) THEN
    RAISE EXCEPTION 'Credential rotation is already open' USING ERRCODE = '55000';
  END IF;
  IF v_node.mode <> 'shadow_readonly'
    OR EXISTS (
      SELECT 1 FROM public.sync_writer_activation
      WHERE writer_node_id = p_node_id AND state IN ('prepared', 'ready')
    )
  THEN
    RAISE EXCEPTION 'Writer or handover node cannot rotate credentials'
      USING ERRCODE = '55000';
  END IF;

  UPDATE public.sync_node
  SET lifecycle_version = lifecycle_version + 1,
      updated_by = p_actor_user_id
  WHERE id = p_node_id
  RETURNING * INTO v_node;

  INSERT INTO public.sync_node_credential_rotation (
    id, tenant_id, branch_id, node_id, status,
    credential_kid, credential_hash, credential_issued_at,
    credential_expires_at, activate_before, requested_by,
    reason_code, reason, created_at, updated_at
  ) VALUES (
    p_operation_id, v_node.tenant_id, v_node.branch_id, p_node_id, 'pending',
    p_credential_kid, p_credential_hash, v_now,
    p_credential_expires_at, v_now + INTERVAL '24 hours', p_actor_user_id,
    p_reason_code, pg_catalog.btrim(p_reason), v_now, v_now
  );

  INSERT INTO public.sync_node_admin_event (
    tenant_id, branch_id, node_id, actor_user_id, operation_id,
    event_type, node_version, request_hash, reason_code, reason, created_at
  ) VALUES (
    v_node.tenant_id, v_node.branch_id, p_node_id, p_actor_user_id, p_operation_id,
    'credential_rotation_started', v_node.lifecycle_version, p_request_hash,
    p_reason_code, pg_catalog.btrim(p_reason), v_now
  );

  RETURN QUERY SELECT
    p_operation_id, p_node_id, 'pending'::TEXT, v_node.lifecycle_version,
    v_now, p_credential_expires_at, v_now + INTERVAL '24 hours',
    NULL::TIMESTAMPTZ, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


TRANSITION_ROTATION_SQL = r"""
CREATE FUNCTION public.transition_sync_node_credential_rotation(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_rotation_id UUID,
  p_expected_version INTEGER,
  p_operation_id UUID,
  p_action TEXT,
  p_confirmation_name TEXT,
  p_request_hash TEXT,
  p_reason_code TEXT,
  p_reason TEXT
) RETURNS TABLE(
  rotation_id UUID,
  node_id UUID,
  rotation_status TEXT,
  node_status TEXT,
  node_version INTEGER,
  applied BOOLEAN
) AS $$
DECLARE
  v_now TIMESTAMPTZ := pg_catalog.statement_timestamp();
  v_event_type TEXT;
  v_rotation public.sync_node_credential_rotation%ROWTYPE;
  v_node public.sync_node%ROWTYPE;
BEGIN
  IF NOT public.platform_actor_has_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.sync.manage'
  ) THEN
    RAISE EXCEPTION 'Recent sync management capability required'
      USING ERRCODE = '42501';
  END IF;

  v_event_type := CASE p_action
    WHEN 'complete' THEN 'credential_rotation_completed'
    WHEN 'cancel' THEN 'credential_rotation_cancelled'
    ELSE NULL
  END;
  IF v_event_type IS NULL
    OR p_expected_version < 1
    OR p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_reason_code NOT IN (
      'routine_maintenance', 'credential_expiry', 'security_incident',
      'device_replacement', 'device_retired', 'other'
    )
    OR pg_catalog.char_length(pg_catalog.btrim(p_reason)) NOT BETWEEN 10 AND 500
  THEN
    RAISE EXCEPTION 'Invalid credential rotation transition' USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 0)
  );

  IF EXISTS (
    SELECT 1 FROM public.sync_node_admin_event AS event
    WHERE event.operation_id = p_operation_id
      AND event.event_type = v_event_type
      AND event.node_id = (
        SELECT rotation.node_id
        FROM public.sync_node_credential_rotation AS rotation
        WHERE rotation.id = p_rotation_id
      )
      AND event.request_hash = p_request_hash
  ) THEN
    RETURN QUERY
    SELECT
      rotation.id, rotation.node_id,
      CASE p_action WHEN 'complete' THEN 'completed' ELSE 'cancelled' END,
      'active'::TEXT, event.node_version, false
    FROM public.sync_node_credential_rotation AS rotation
    JOIN public.sync_node_admin_event AS event
      ON event.operation_id = p_operation_id
     AND event.node_id = rotation.node_id
     AND event.event_type = v_event_type
     AND event.request_hash = p_request_hash
    WHERE rotation.id = p_rotation_id;
    RETURN;
  ELSIF EXISTS (
    SELECT 1 FROM public.sync_node_admin_event AS event
    WHERE event.operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Operation identifier is already used' USING ERRCODE = '23505';
  END IF;

  SELECT * INTO v_rotation
  FROM public.sync_node_credential_rotation
  WHERE id = p_rotation_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Credential rotation not found' USING ERRCODE = 'P0002';
  END IF;

  SELECT * INTO v_node
  FROM public.sync_node
  WHERE id = v_rotation.node_id AND node_kind = 'edge'
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Edge node not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_node.lifecycle_version <> p_expected_version THEN
    RETURN;
  END IF;
  IF v_node.display_name IS DISTINCT FROM p_confirmation_name THEN
    RAISE EXCEPTION 'Edge node confirmation does not match' USING ERRCODE = '55000';
  END IF;
  IF v_node.status <> 'active' THEN
    RAISE EXCEPTION 'Revoked Edge node cannot change credential rotation'
      USING ERRCODE = '55000';
  END IF;

  IF p_action = 'complete' THEN
    IF v_rotation.status <> 'verified' OR v_rotation.activate_before <= v_now THEN
      RAISE EXCEPTION 'New credential must be verified before completion'
        USING ERRCODE = '55000';
    END IF;
    UPDATE public.sync_node
    SET credential_kid = v_rotation.credential_kid,
        credential_hash = v_rotation.credential_hash,
        credential_issued_at = v_rotation.credential_issued_at,
        credential_expires_at = v_rotation.credential_expires_at,
        lifecycle_version = lifecycle_version + 1,
        updated_by = p_actor_user_id
    WHERE id = v_node.id
    RETURNING * INTO v_node;
    UPDATE public.sync_node_credential_rotation
    SET status = 'completed', completed_at = v_now, updated_at = v_now
    WHERE id = p_rotation_id
    RETURNING * INTO v_rotation;
  ELSE
    IF v_rotation.status NOT IN ('pending', 'verified') THEN
      RAISE EXCEPTION 'Credential rotation is not open' USING ERRCODE = '55000';
    END IF;
    UPDATE public.sync_node
    SET lifecycle_version = lifecycle_version + 1,
        updated_by = p_actor_user_id
    WHERE id = v_node.id
    RETURNING * INTO v_node;
    UPDATE public.sync_node_credential_rotation
    SET status = 'cancelled', cancelled_at = v_now, updated_at = v_now
    WHERE id = p_rotation_id
    RETURNING * INTO v_rotation;
  END IF;

  INSERT INTO public.sync_node_admin_event (
    tenant_id, branch_id, node_id, actor_user_id, operation_id,
    event_type, node_version, request_hash, reason_code, reason, created_at
  ) VALUES (
    v_node.tenant_id, v_node.branch_id, v_node.id, p_actor_user_id, p_operation_id,
    v_event_type, v_node.lifecycle_version, p_request_hash,
    p_reason_code, pg_catalog.btrim(p_reason), v_now
  );

  RETURN QUERY SELECT
    v_rotation.id, v_node.id, v_rotation.status, v_node.status,
    v_node.lifecycle_version, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


REVOKE_NODE_SQL = r"""
CREATE FUNCTION public.revoke_sync_node(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_node_id UUID,
  p_expected_version INTEGER,
  p_operation_id UUID,
  p_confirmation_name TEXT,
  p_request_hash TEXT,
  p_reason_code TEXT,
  p_reason TEXT
) RETURNS TABLE(
  node_id UUID,
  node_status TEXT,
  node_version INTEGER,
  applied BOOLEAN
) AS $$
DECLARE
  v_now TIMESTAMPTZ := pg_catalog.statement_timestamp();
  v_node public.sync_node%ROWTYPE;
BEGIN
  IF NOT public.platform_actor_has_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.sync.manage'
  ) THEN
    RAISE EXCEPTION 'Recent sync management capability required'
      USING ERRCODE = '42501';
  END IF;
  IF p_expected_version < 1
    OR p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_reason_code NOT IN (
      'routine_maintenance', 'credential_expiry', 'security_incident',
      'device_replacement', 'device_retired', 'other'
    )
    OR pg_catalog.char_length(pg_catalog.btrim(p_reason)) NOT BETWEEN 10 AND 500
  THEN
    RAISE EXCEPTION 'Invalid Edge node revocation' USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 0)
  );

  IF EXISTS (
    SELECT 1 FROM public.sync_node_admin_event AS event
    WHERE event.operation_id = p_operation_id
      AND event.node_id = p_node_id
      AND event.event_type = 'node_revoked'
      AND event.request_hash = p_request_hash
  ) THEN
    RETURN QUERY
    SELECT sync_node.id, 'revoked'::TEXT, event.node_version, false
    FROM public.sync_node
    JOIN public.sync_node_admin_event AS event
      ON event.operation_id = p_operation_id
     AND event.node_id = sync_node.id
     AND event.event_type = 'node_revoked'
     AND event.request_hash = p_request_hash
    WHERE sync_node.id = p_node_id;
    RETURN;
  ELSIF EXISTS (
    SELECT 1 FROM public.sync_node_admin_event AS event
    WHERE event.operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Operation identifier is already used' USING ERRCODE = '23505';
  END IF;

  SELECT * INTO v_node
  FROM public.sync_node
  WHERE id = p_node_id AND node_kind = 'edge'
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Edge node not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_node.lifecycle_version <> p_expected_version THEN
    RETURN;
  END IF;
  IF v_node.display_name IS DISTINCT FROM p_confirmation_name THEN
    RAISE EXCEPTION 'Edge node confirmation does not match' USING ERRCODE = '55000';
  END IF;
  IF v_node.status <> 'active' THEN
    RAISE EXCEPTION 'Edge node is already revoked' USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.sync_writer_epoch
    WHERE writer_node_id = p_node_id AND state = 'active'
  ) OR EXISTS (
    SELECT 1 FROM public.sync_writer_activation
    WHERE writer_node_id = p_node_id AND state IN ('prepared', 'ready')
  ) THEN
    RAISE EXCEPTION 'Writer Edge node cannot be revoked' USING ERRCODE = '55000';
  END IF;

  UPDATE public.sync_node
  SET status = 'revoked',
      lifecycle_version = lifecycle_version + 1,
      updated_by = p_actor_user_id
  WHERE id = p_node_id
  RETURNING * INTO v_node;

  UPDATE public.sync_node_credential_rotation AS rotation
  SET status = 'cancelled', cancelled_at = v_now, updated_at = v_now
  WHERE rotation.node_id = p_node_id
    AND rotation.status IN ('pending', 'verified');

  INSERT INTO public.sync_node_admin_event (
    tenant_id, branch_id, node_id, actor_user_id, operation_id,
    event_type, node_version, request_hash, reason_code, reason, created_at
  ) VALUES (
    v_node.tenant_id, v_node.branch_id, v_node.id, p_actor_user_id, p_operation_id,
    'node_revoked', v_node.lifecycle_version, p_request_hash,
    p_reason_code, pg_catalog.btrim(p_reason), v_now
  );

  RETURN QUERY SELECT v_node.id, v_node.status, v_node.lifecycle_version, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


IMMUTABLE_EVENT_SQL = r"""
CREATE FUNCTION public.trg_reject_sync_node_admin_event_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'Sync node administration events are immutable'
    USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


AUDIT_EVENT_SQL = r"""
CREATE FUNCTION public.trg_audit_sync_node_admin_event()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.audit_log (
    tenant_id, user_id, action, table_name, record_id, metadata, created_at
  ) VALUES (
    NEW.tenant_id,
    NEW.actor_user_id,
    'INSERT',
    'sync_node_admin_event',
    NEW.node_id,
    jsonb_build_object(
      'event_type', NEW.event_type,
      'operation_id', NEW.operation_id,
      'node_version', NEW.node_version,
      'reason_code', NEW.reason_code
    ),
    NEW.created_at
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


HANDOVER_ROTATION_GUARD_SQL = r"""
CREATE FUNCTION public.trg_guard_sync_handover_credential_rotation()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.state IN ('prepared', 'ready') THEN
    PERFORM 1
    FROM public.sync_node
    WHERE id = NEW.writer_node_id
    FOR UPDATE;

    IF EXISTS (
      SELECT 1 FROM public.sync_node_credential_rotation AS rotation
      WHERE rotation.node_id = NEW.writer_node_id
        AND rotation.status IN ('pending', 'verified')
    ) THEN
      RAISE EXCEPTION 'Credential rotation must finish before writer handover'
        USING ERRCODE = '55000';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


def _secure_function(signature: str, *, grant_to: str | None = None) -> None:
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} " "FROM PUBLIC, aurum_app, aurum_support"
    )
    if grant_to is not None:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grant_to}")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.sync_node
          ADD COLUMN lifecycle_version INTEGER NOT NULL DEFAULT 1
            CHECK (lifecycle_version >= 1)
        """)
    op.execute("""
        CREATE TABLE public.sync_node_credential_rotation (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES public.tenant(id) ON DELETE RESTRICT,
          branch_id UUID NOT NULL REFERENCES public.branch(id) ON DELETE RESTRICT,
          node_id UUID NOT NULL REFERENCES public.sync_node(id) ON DELETE RESTRICT,
          status TEXT NOT NULL CHECK (status IN ('pending','verified','completed','cancelled')),
          credential_kid UUID NOT NULL UNIQUE,
          credential_hash TEXT NOT NULL UNIQUE CHECK (credential_hash ~ '^[0-9a-f]{64}$'),
          credential_issued_at TIMESTAMPTZ NOT NULL,
          credential_expires_at TIMESTAMPTZ NOT NULL,
          activate_before TIMESTAMPTZ NOT NULL,
          verified_at TIMESTAMPTZ,
          completed_at TIMESTAMPTZ,
          cancelled_at TIMESTAMPTZ,
          requested_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          reason_code TEXT NOT NULL CHECK (reason_code IN (
            'routine_maintenance', 'credential_expiry', 'security_incident',
            'device_replacement', 'device_retired', 'other'
          )),
          reason TEXT NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 10 AND 500),
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CHECK (credential_expires_at > credential_issued_at),
          CHECK (activate_before > credential_issued_at),
          CHECK (
            (status = 'pending' AND verified_at IS NULL)
            OR (status IN ('verified','completed') AND verified_at IS NOT NULL)
            OR status = 'cancelled'
          ),
          CHECK ((status = 'completed') = (completed_at IS NOT NULL)),
          CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL))
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_sync_node_open_credential_rotation
        ON public.sync_node_credential_rotation(node_id)
        WHERE status IN ('pending', 'verified')
        """)
    op.execute("""
        CREATE INDEX ix_sync_node_rotation_node_created
        ON public.sync_node_credential_rotation(node_id, created_at DESC)
        """)
    op.execute("""
        CREATE TABLE public.sync_node_admin_event (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES public.tenant(id) ON DELETE RESTRICT,
          branch_id UUID NOT NULL REFERENCES public.branch(id) ON DELETE RESTRICT,
          node_id UUID NOT NULL REFERENCES public.sync_node(id) ON DELETE RESTRICT,
          actor_user_id UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          operation_id UUID NOT NULL UNIQUE,
          event_type TEXT NOT NULL CHECK (event_type IN (
            'credential_rotation_started', 'credential_rotation_verified',
            'credential_rotation_completed', 'credential_rotation_cancelled',
            'node_revoked'
          )),
          node_version INTEGER NOT NULL CHECK (node_version >= 1),
          request_hash TEXT CHECK (request_hash ~ '^[0-9a-f]{64}$'),
          reason_code TEXT,
          reason TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CHECK (
            (event_type = 'credential_rotation_verified'
             AND request_hash IS NULL AND reason_code IS NULL AND reason IS NULL)
            OR
            (event_type <> 'credential_rotation_verified'
             AND request_hash IS NOT NULL
             AND reason_code IN (
               'routine_maintenance', 'credential_expiry', 'security_incident',
               'device_replacement', 'device_retired', 'other'
             )
             AND char_length(btrim(reason)) BETWEEN 10 AND 500)
          )
        )
        """)
    op.execute("""
        CREATE INDEX ix_sync_node_admin_event_node_created
        ON public.sync_node_admin_event(node_id, created_at DESC, id)
        """)

    for table in ("sync_node_credential_rotation", "sync_node_admin_event"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON public.{table}
            USING (
              tenant_id = public.current_tenant_id()
              OR SESSION_USER = 'aurum_support'
            )
            WITH CHECK (
              tenant_id = public.current_tenant_id()
              OR SESSION_USER = 'aurum_support'
            )
            """)

    op.execute(IMMUTABLE_EVENT_SQL)
    _secure_function("public.trg_reject_sync_node_admin_event_mutation()")
    op.execute("""
        CREATE TRIGGER trg_immutable_sync_node_admin_event
        BEFORE UPDATE OR DELETE ON public.sync_node_admin_event
        FOR EACH ROW EXECUTE FUNCTION public.trg_reject_sync_node_admin_event_mutation()
        """)
    op.execute(AUDIT_EVENT_SQL)
    _secure_function("public.trg_audit_sync_node_admin_event()")
    op.execute("""
        CREATE TRIGGER trg_audit_sync_node_admin_event
        AFTER INSERT ON public.sync_node_admin_event
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_sync_node_admin_event()
        """)
    op.execute(HANDOVER_ROTATION_GUARD_SQL)
    _secure_function("public.trg_guard_sync_handover_credential_rotation()")
    op.execute("""
        CREATE TRIGGER trg_guard_sync_handover_credential_rotation
        BEFORE INSERT OR UPDATE OF state, writer_node_id
        ON public.sync_writer_activation
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_handover_credential_rotation()
        """)

    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.sync_node_credential_rotation, "
        "public.sync_node_admin_event FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT ON TABLE public.sync_node_credential_rotation, "
        "public.sync_node_admin_event TO aurum_support"
    )

    op.execute(PREPARE_ROTATION_SQL)
    _secure_function(
        "public.prepare_sync_node_credential_rotation("
        "UUID, UUID, UUID, INTEGER, UUID, UUID, TEXT, TIMESTAMPTZ, "
        "TEXT, TEXT, TEXT, TEXT)",
        grant_to="aurum_support",
    )
    op.execute(TRANSITION_ROTATION_SQL)
    _secure_function(
        "public.transition_sync_node_credential_rotation("
        "UUID, UUID, UUID, INTEGER, UUID, TEXT, TEXT, TEXT, TEXT, TEXT)",
        grant_to="aurum_support",
    )
    op.execute(REVOKE_NODE_SQL)
    _secure_function(
        "public.revoke_sync_node(" "UUID, UUID, UUID, INTEGER, UUID, TEXT, TEXT, TEXT, TEXT)",
        grant_to="aurum_support",
    )
    op.execute(AUTHENTICATE_EDGE_NODE_SQL)
    _secure_function(
        "public.authenticate_edge_node(UUID, TEXT)",
        grant_to="aurum_app, aurum_support",
    )


def downgrade() -> None:
    op.execute(AUTHENTICATE_EDGE_NODE_DOWN_SQL)
    _secure_function(
        "public.authenticate_edge_node(UUID, TEXT)",
        grant_to="aurum_app, aurum_support",
    )
    op.execute(
        "DROP FUNCTION public.revoke_sync_node("
        "UUID, UUID, UUID, INTEGER, UUID, TEXT, TEXT, TEXT, TEXT)"
    )
    op.execute(
        "DROP FUNCTION public.transition_sync_node_credential_rotation("
        "UUID, UUID, UUID, INTEGER, UUID, TEXT, TEXT, TEXT, TEXT, TEXT)"
    )
    op.execute(
        "DROP FUNCTION public.prepare_sync_node_credential_rotation("
        "UUID, UUID, UUID, INTEGER, UUID, UUID, TEXT, TIMESTAMPTZ, "
        "TEXT, TEXT, TEXT, TEXT)"
    )
    op.execute(
        "DROP TRIGGER trg_guard_sync_handover_credential_rotation "
        "ON public.sync_writer_activation"
    )
    op.execute("DROP FUNCTION public.trg_guard_sync_handover_credential_rotation()")
    op.execute("DROP TRIGGER trg_audit_sync_node_admin_event ON public.sync_node_admin_event")
    op.execute("DROP FUNCTION public.trg_audit_sync_node_admin_event()")
    op.execute("DROP TRIGGER trg_immutable_sync_node_admin_event ON public.sync_node_admin_event")
    op.execute("DROP FUNCTION public.trg_reject_sync_node_admin_event_mutation()")
    op.execute("DROP TABLE public.sync_node_admin_event")
    op.execute("DROP TABLE public.sync_node_credential_rotation")
    op.execute("ALTER TABLE public.sync_node DROP COLUMN lifecycle_version")
