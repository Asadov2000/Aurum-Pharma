"""sync: add guarded writer handover protocol

Revision ID: 0045
Revises: 0044
Create Date: 2026-07-15

Preparation creates a quiescent branch boundary, readiness is recorded only by
the assigned Edge identity, and activation changes ownership in one database
transaction. Operational writes lock the current stream in share mode so they
cannot cross an ownership transition unnoticed.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0045"
down_revision: str | Sequence[str] | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INITIALIZE_BRANCH_WRITER_SQL = """
CREATE FUNCTION public.initialize_branch_sync_writer(
  p_tenant_id UUID,
  p_branch_id UUID
) RETURNS VOID AS $$
DECLARE
  v_previous_branch TEXT;
BEGIN
  IF p_tenant_id IS NULL OR p_branch_id IS NULL THEN
    RAISE EXCEPTION 'Invalid branch writer scope'
      USING ERRCODE = '22023';
  END IF;

  IF SESSION_USER = 'aurum_app'
    AND public.current_tenant_id() IS DISTINCT FROM p_tenant_id
  THEN
    RAISE EXCEPTION 'Invalid branch writer scope'
      USING ERRCODE = '42501';
  ELSIF SESSION_USER NOT IN ('aurum_app', 'aurum_support') THEN
    RAISE EXCEPTION 'Branch writer initialization is not allowed'
      USING ERRCODE = '42501';
  END IF;

  v_previous_branch := pg_catalog.current_setting('app.branch_id', true);
  PERFORM pg_catalog.set_config('app.branch_id', p_branch_id::TEXT, true);

  INSERT INTO public.sync_node (
    tenant_id,
    branch_id,
    node_kind,
    mode,
    status,
    display_name
  ) VALUES (
    p_tenant_id,
    p_branch_id,
    'cloud',
    'cloud_writer',
    'active',
    'Cloud writer'
  )
  ON CONFLICT (tenant_id, branch_id)
    WHERE node_kind = 'cloud'
  DO NOTHING;

  INSERT INTO public.sync_stream (
    tenant_id,
    branch_id,
    writer_node_id,
    writer_epoch,
    last_sequence,
    current_checksum,
    current_projection_checksum
  )
  SELECT
    p_tenant_id,
    p_branch_id,
    sync_node.id,
    1,
    0,
    repeat('0', 64),
    repeat('0', 64)
  FROM public.sync_node AS sync_node
  WHERE sync_node.tenant_id = p_tenant_id
    AND sync_node.branch_id = p_branch_id
    AND sync_node.node_kind = 'cloud'
  ON CONFLICT (tenant_id, branch_id) DO NOTHING;

  PERFORM pg_catalog.set_config(
    'app.branch_id',
    COALESCE(v_previous_branch, ''),
    true
  );
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


INITIALIZE_BRANCH_TRIGGER_SQL = """
CREATE FUNCTION public.trg_initialize_branch_sync_writer() RETURNS TRIGGER AS $$
BEGIN
  PERFORM public.initialize_branch_sync_writer(NEW.tenant_id, NEW.id);
  RETURN NULL;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ASSERT_BRANCH_WRITER_SQL = """
CREATE FUNCTION public.assert_current_branch_writer(
  p_tenant_id UUID,
  p_branch_id UUID,
  p_register_id UUID,
  p_allow_edge BOOLEAN
) RETURNS VOID AS $$
DECLARE
  v_edge_node_id UUID;
  v_writer_node_id UUID;
  v_writer_epoch BIGINT;
  v_node_kind TEXT;
  v_node_mode TEXT;
  v_node_status TEXT;
  v_capability TEXT;
  v_epoch_state TEXT;
  v_allowed_register_id UUID;
BEGIN
  IF p_tenant_id IS NULL OR p_branch_id IS NULL THEN
    RAISE EXCEPTION 'Invalid branch writer scope'
      USING ERRCODE = '22023';
  END IF;

  IF SESSION_USER = 'aurum_app' THEN
    IF public.current_tenant_id() IS DISTINCT FROM p_tenant_id THEN
      RAISE EXCEPTION 'Branch writer tenant scope does not match'
        USING ERRCODE = '42501';
    END IF;

    IF NULLIF(pg_catalog.current_setting('app.branch_id', true), '') IS NOT NULL
      AND NULLIF(
        pg_catalog.current_setting('app.branch_id', true),
        ''
      )::UUID IS DISTINCT FROM p_branch_id
    THEN
      RAISE EXCEPTION 'Branch writer branch scope does not match'
        USING ERRCODE = '42501';
    END IF;
  ELSIF SESSION_USER <> 'aurum_support' THEN
    RAISE EXCEPTION 'Branch writer access is not allowed'
      USING ERRCODE = '42501';
  END IF;

  SELECT
    sync_stream.writer_node_id,
    sync_stream.writer_epoch,
    sync_node.node_kind,
    sync_node.mode,
    sync_node.status,
    writer_epoch.capability,
    writer_epoch.state,
    writer_epoch.allowed_register_id
  INTO
    v_writer_node_id,
    v_writer_epoch,
    v_node_kind,
    v_node_mode,
    v_node_status,
    v_capability,
    v_epoch_state,
    v_allowed_register_id
  FROM public.sync_stream AS sync_stream
  JOIN public.sync_node AS sync_node
    ON sync_node.id = sync_stream.writer_node_id
   AND sync_node.tenant_id = sync_stream.tenant_id
   AND sync_node.branch_id = sync_stream.branch_id
  JOIN public.sync_writer_epoch AS writer_epoch
    ON writer_epoch.tenant_id = sync_stream.tenant_id
   AND writer_epoch.branch_id = sync_stream.branch_id
   AND writer_epoch.writer_epoch = sync_stream.writer_epoch
   AND writer_epoch.writer_node_id = sync_stream.writer_node_id
  WHERE sync_stream.tenant_id = p_tenant_id
    AND sync_stream.branch_id = p_branch_id
  FOR SHARE OF sync_stream;

  IF v_writer_node_id IS NULL
    OR v_epoch_state <> 'active'
    OR v_node_status <> 'active'
  THEN
    RAISE EXCEPTION 'Active branch writer is unavailable'
      USING ERRCODE = '55000';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.sync_writer_activation AS activation
    WHERE activation.tenant_id = p_tenant_id
      AND activation.branch_id = p_branch_id
      AND activation.state IN ('prepared', 'ready')
  ) THEN
    RAISE EXCEPTION 'Branch writer handover is in progress'
      USING ERRCODE = '55000';
  END IF;

  v_edge_node_id := NULLIF(
    pg_catalog.current_setting('app.edge_node_id', true),
    ''
  )::UUID;

  IF v_edge_node_id IS NULL THEN
    IF v_node_kind <> 'cloud'
      OR v_node_mode <> 'cloud_writer'
      OR v_capability <> 'cloud_full'
    THEN
      RAISE EXCEPTION 'Branch is assigned to an Edge writer'
        USING ERRCODE = '55000';
    END IF;
    RETURN;
  END IF;

  IF NOT p_allow_edge
    OR v_writer_node_id IS DISTINCT FROM v_edge_node_id
    OR v_node_kind <> 'edge'
    OR v_node_mode <> 'edge_writer'
    OR v_capability <> 'cash_sale_v1'
    OR (
      p_register_id IS NOT NULL
      AND v_allowed_register_id IS DISTINCT FROM p_register_id
    )
  THEN
    RAISE EXCEPTION 'Edge node does not own this branch operation'
      USING ERRCODE = '42501';
  END IF;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


BRANCH_WRITE_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_branch_writer() RETURNS TRIGGER AS $$
DECLARE
  v_old_row JSONB;
  v_new_row JSONB;
  v_old_tenant_id UUID;
  v_old_branch_id UUID;
  v_old_register_id UUID;
  v_new_tenant_id UUID;
  v_new_branch_id UUID;
  v_new_register_id UUID;
  v_allow_edge BOOLEAN;
BEGIN
  IF TG_OP = 'DELETE'
    AND SESSION_USER = 'aurum_support'
    AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
  THEN
    RETURN OLD;
  END IF;

  v_allow_edge := TG_TABLE_NAME IN ('shift', 'sale', 'batch');

  IF TG_OP <> 'INSERT' THEN
    v_old_row := pg_catalog.to_jsonb(OLD);
    v_old_tenant_id := (v_old_row ->> 'tenant_id')::UUID;
    v_old_branch_id := (v_old_row ->> 'branch_id')::UUID;
    v_old_register_id := NULLIF(v_old_row ->> 'register_id', '')::UUID;
    PERFORM public.assert_current_branch_writer(
      v_old_tenant_id,
      v_old_branch_id,
      v_old_register_id,
      v_allow_edge
    );
  END IF;

  IF TG_OP <> 'DELETE' THEN
    v_new_row := pg_catalog.to_jsonb(NEW);
    v_new_tenant_id := (v_new_row ->> 'tenant_id')::UUID;
    v_new_branch_id := (v_new_row ->> 'branch_id')::UUID;
    v_new_register_id := NULLIF(v_new_row ->> 'register_id', '')::UUID;

    IF TG_OP = 'INSERT'
      OR v_new_tenant_id IS DISTINCT FROM v_old_tenant_id
      OR v_new_branch_id IS DISTINCT FROM v_old_branch_id
      OR v_new_register_id IS DISTINCT FROM v_old_register_id
    THEN
      PERFORM public.assert_current_branch_writer(
        v_new_tenant_id,
        v_new_branch_id,
        v_new_register_id,
        v_allow_edge
      );
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


OUTBOX_WRITE_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sync_outbox_writer() RETURNS TRIGGER AS $$
DECLARE
  v_stream public.sync_stream%ROWTYPE;
BEGIN
  IF TG_OP = 'DELETE'
    AND SESSION_USER = 'aurum_support'
    AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
  THEN
    RETURN OLD;
  END IF;

  IF TG_OP <> 'INSERT' THEN
    PERFORM public.assert_current_branch_writer(
      OLD.tenant_id,
      OLD.branch_id,
      NULL,
      true
    );
  END IF;

  IF TG_OP <> 'DELETE' THEN
    PERFORM public.assert_current_branch_writer(
      NEW.tenant_id,
      NEW.branch_id,
      NULL,
      true
    );

    SELECT sync_stream.*
    INTO v_stream
    FROM public.sync_stream AS sync_stream
    WHERE sync_stream.tenant_id = NEW.tenant_id
      AND sync_stream.branch_id = NEW.branch_id;

    IF v_stream.writer_node_id IS DISTINCT FROM NEW.origin_node_id
      OR v_stream.writer_epoch IS DISTINCT FROM NEW.writer_epoch
    THEN
      RAISE EXCEPTION 'Outbox event does not belong to the active writer epoch'
        USING ERRCODE = '42501';
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


SYNC_STREAM_SCOPE_GUARD_SQL = """
CREATE OR REPLACE FUNCTION public.trg_guard_sync_stream_scope() RETURNS TRIGGER AS $$
BEGIN
  IF SESSION_USER = 'aurum_app' THEN
    IF TG_OP = 'INSERT' THEN
      IF public.current_tenant_id() IS NULL
        OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
        OR NULLIF(
          pg_catalog.current_setting('app.branch_id', true),
          ''
        )::UUID IS DISTINCT FROM NEW.branch_id
        OR NULLIF(
          pg_catalog.current_setting('app.edge_node_id', true),
          ''
        ) IS NOT NULL
      THEN
        RAISE EXCEPTION 'Sync stream writes require a Cloud branch scope'
          USING ERRCODE = '42501';
      END IF;
    ELSE
      IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
        OR NEW.branch_id IS DISTINCT FROM OLD.branch_id
        OR NEW.writer_node_id IS DISTINCT FROM OLD.writer_node_id
        OR NEW.writer_epoch IS DISTINCT FROM OLD.writer_epoch
      THEN
        RAISE EXCEPTION 'Runtime cannot change branch writer ownership'
          USING ERRCODE = '42501';
      END IF;

      PERFORM public.assert_current_branch_writer(
        NEW.tenant_id,
        NEW.branch_id,
        NULL,
        true
      );
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RESERVE_SYNC_EVENT_POSITION_SQL = """
CREATE OR REPLACE FUNCTION public.reserve_sync_event_position(
  p_tenant_id UUID,
  p_branch_id UUID
) RETURNS TABLE(
  stream_id UUID,
  origin_node_id UUID,
  writer_epoch BIGINT,
  sequence BIGINT,
  previous_checksum TEXT,
  previous_projection_checksum TEXT
) AS $$
DECLARE
  v_stream_id UUID;
  v_origin_node_id UUID;
  v_writer_epoch BIGINT;
  v_previous_checksum TEXT;
  v_previous_projection_checksum TEXT;
  v_sequence BIGINT;
BEGIN
  PERFORM public.assert_current_branch_writer(
    p_tenant_id,
    p_branch_id,
    NULL,
    false
  );

  SELECT
    sync_stream.id,
    sync_stream.writer_node_id,
    sync_stream.writer_epoch,
    sync_stream.current_checksum,
    sync_stream.current_projection_checksum
  INTO
    v_stream_id,
    v_origin_node_id,
    v_writer_epoch,
    v_previous_checksum,
    v_previous_projection_checksum
  FROM public.sync_stream AS sync_stream
  WHERE sync_stream.tenant_id = p_tenant_id
    AND sync_stream.branch_id = p_branch_id
  FOR UPDATE;

  IF v_stream_id IS NULL THEN
    RAISE EXCEPTION 'Sync stream is unavailable'
      USING ERRCODE = '55000';
  END IF;

  UPDATE public.sync_stream AS sync_stream
  SET last_sequence = sync_stream.last_sequence + 1
  WHERE sync_stream.id = v_stream_id
  RETURNING sync_stream.last_sequence INTO v_sequence;

  RETURN QUERY
  SELECT
    v_stream_id,
    v_origin_node_id,
    v_writer_epoch,
    v_sequence,
    v_previous_checksum,
    v_previous_projection_checksum;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ALLOCATE_REGISTER_RECEIPT_SQL = """
CREATE OR REPLACE FUNCTION public.allocate_register_receipt(
  p_tenant_id UUID,
  p_register_id UUID
) RETURNS TABLE(
  receipt_seq BIGINT,
  receipt_number TEXT
) AS $$
DECLARE
  v_branch_id UUID;
  v_writer_epoch BIGINT;
  v_receipt_seq BIGINT;
BEGIN
  SELECT register.branch_id
  INTO v_branch_id
  FROM public.register AS register
  WHERE register.id = p_register_id
    AND register.tenant_id = p_tenant_id;

  IF v_branch_id IS NULL THEN
    RETURN;
  END IF;

  PERFORM public.assert_current_branch_writer(
    p_tenant_id,
    v_branch_id,
    p_register_id,
    false
  );

  PERFORM register.id
  FROM public.register AS register
  WHERE register.id = p_register_id
    AND register.tenant_id = p_tenant_id
    AND register.branch_id = v_branch_id
  FOR UPDATE;

  SELECT sync_stream.writer_epoch
  INTO v_writer_epoch
  FROM public.sync_stream AS sync_stream
  WHERE sync_stream.tenant_id = p_tenant_id
    AND sync_stream.branch_id = v_branch_id
  FOR UPDATE;

  INSERT INTO public.register_receipt_counter (
    tenant_id,
    branch_id,
    register_id,
    writer_epoch,
    last_receipt_seq
  ) VALUES (
    p_tenant_id,
    v_branch_id,
    p_register_id,
    v_writer_epoch,
    0
  )
  ON CONFLICT (tenant_id, register_id) DO NOTHING;

  UPDATE public.register_receipt_counter AS counter
  SET
    writer_epoch = v_writer_epoch,
    last_receipt_seq = counter.last_receipt_seq + 1
  WHERE counter.tenant_id = p_tenant_id
    AND counter.branch_id = v_branch_id
    AND counter.register_id = p_register_id
  RETURNING counter.last_receipt_seq INTO v_receipt_seq;

  IF v_receipt_seq IS NULL THEN
    RAISE EXCEPTION 'Receipt counter is unavailable'
      USING ERRCODE = '55000';
  END IF;

  RETURN QUERY
  SELECT v_receipt_seq, pg_catalog.lpad(v_receipt_seq::TEXT, 6, '0');
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


AUTHENTICATE_EDGE_NODE_SQL = """
CREATE OR REPLACE FUNCTION public.authenticate_edge_node(
  p_credential_kid UUID,
  p_credential_hash TEXT
) RETURNS TABLE(
  node_id UUID,
  tenant_id UUID,
  branch_id UUID,
  shadow_start_sequence BIGINT,
  shadow_start_checksum TEXT,
  shadow_start_projection_checksum TEXT
) AS $$
BEGIN
  IF p_credential_kid IS NULL
    OR p_credential_hash !~ '^[0-9a-f]{64}$'
  THEN
    RETURN;
  END IF;

  RETURN QUERY
  SELECT
    sync_node.id,
    sync_node.tenant_id,
    sync_node.branch_id,
    sync_node.shadow_start_sequence,
    sync_node.shadow_start_checksum,
    sync_node.shadow_start_projection_checksum
  FROM public.sync_node AS sync_node
  WHERE sync_node.node_kind = 'edge'
    AND sync_node.mode IN ('shadow_readonly', 'edge_writer')
    AND sync_node.status = 'active'
    AND sync_node.credential_kid = p_credential_kid
    AND sync_node.credential_hash = p_credential_hash
    AND sync_node.credential_expires_at > pg_catalog.now();

  UPDATE public.sync_node AS sync_node
  SET last_seen_at = pg_catalog.now()
  WHERE sync_node.node_kind = 'edge'
    AND sync_node.mode IN ('shadow_readonly', 'edge_writer')
    AND sync_node.status = 'active'
    AND sync_node.credential_kid = p_credential_kid
    AND sync_node.credential_hash = p_credential_hash
    AND sync_node.credential_expires_at > pg_catalog.now();
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


PREPARE_HANDOVER_SQL = """
CREATE FUNCTION public.prepare_edge_writer_handover(
  p_activation_id UUID,
  p_tenant_id UUID,
  p_branch_id UUID,
  p_edge_node_id UUID,
  p_register_id UUID,
  p_expected_writer_epoch BIGINT,
  p_expected_sequence BIGINT,
  p_expected_source_checksum TEXT,
  p_expected_projection_checksum TEXT,
  p_bootstrap_snapshot_hash TEXT,
  p_request_hash TEXT
) RETURNS UUID AS $$
DECLARE
  v_stream public.sync_stream%ROWTYPE;
  v_existing public.sync_writer_activation%ROWTYPE;
  v_current public.sync_writer_epoch%ROWTYPE;
  v_edge public.sync_node%ROWTYPE;
  v_next_epoch BIGINT;
  v_receipt_baseline BIGINT;
  v_manifest_hash TEXT;
  v_root_source_checksum TEXT;
  v_root_projection_checksum TEXT;
BEGIN
  IF SESSION_USER <> 'aurum_support'
    OR COALESCE(
      pg_catalog.current_setting('app.support_session', true),
      'false'
    ) <> 'true'
  THEN
    RAISE EXCEPTION 'Writer handover preparation requires a support session'
      USING ERRCODE = '42501';
  END IF;

  IF p_activation_id IS NULL
    OR p_tenant_id IS NULL
    OR p_branch_id IS NULL
    OR p_edge_node_id IS NULL
    OR p_register_id IS NULL
    OR p_expected_writer_epoch <= 0
    OR p_expected_sequence < 0
    OR p_expected_source_checksum !~ '^[0-9a-f]{64}$'
    OR p_expected_projection_checksum !~ '^[0-9a-f]{64}$'
    OR p_bootstrap_snapshot_hash !~ '^[0-9a-f]{64}$'
    OR p_request_hash !~ '^[0-9a-f]{64}$'
  THEN
    RAISE EXCEPTION 'Invalid writer handover preparation'
      USING ERRCODE = '22023';
  END IF;

  SELECT sync_stream.*
  INTO v_stream
  FROM public.sync_stream AS sync_stream
  WHERE sync_stream.tenant_id = p_tenant_id
    AND sync_stream.branch_id = p_branch_id
  FOR UPDATE;

  SELECT writer_epoch.*
  INTO v_existing
  FROM public.sync_writer_activation AS writer_epoch
  WHERE writer_epoch.activation_id = p_activation_id
  FOR UPDATE;

  IF FOUND THEN
    IF v_existing.tenant_id IS DISTINCT FROM p_tenant_id
      OR v_existing.branch_id IS DISTINCT FROM p_branch_id
      OR v_existing.writer_node_id IS DISTINCT FROM p_edge_node_id
      OR v_existing.allowed_register_id IS DISTINCT FROM p_register_id
      OR v_existing.writer_epoch IS DISTINCT FROM p_expected_writer_epoch + 1
      OR v_existing.previous_writer_epoch IS DISTINCT FROM p_expected_writer_epoch
      OR v_existing.previous_terminal_sequence IS DISTINCT FROM p_expected_sequence
      OR v_existing.previous_terminal_source_checksum
        IS DISTINCT FROM p_expected_source_checksum
      OR v_existing.previous_terminal_projection_checksum
        IS DISTINCT FROM p_expected_projection_checksum
      OR v_existing.bootstrap_snapshot_hash IS DISTINCT FROM p_bootstrap_snapshot_hash
      OR v_existing.prepare_request_hash IS DISTINCT FROM p_request_hash
    THEN
      RAISE EXCEPTION 'Activation ID was already used for another handover'
        USING ERRCODE = '55000';
    END IF;
    RETURN v_existing.activation_id;
  END IF;

  IF v_stream.id IS NULL
    OR v_stream.writer_epoch IS DISTINCT FROM p_expected_writer_epoch
    OR v_stream.last_sequence IS DISTINCT FROM p_expected_sequence
    OR v_stream.current_checksum IS DISTINCT FROM p_expected_source_checksum
    OR v_stream.current_projection_checksum
      IS DISTINCT FROM p_expected_projection_checksum
  THEN
    RAISE EXCEPTION 'Cloud writer checkpoint changed before preparation'
      USING ERRCODE = '40001';
  END IF;

  SELECT writer_epoch.*
  INTO v_current
  FROM public.sync_writer_epoch AS writer_epoch
  WHERE writer_epoch.tenant_id = p_tenant_id
    AND writer_epoch.branch_id = p_branch_id
    AND writer_epoch.writer_epoch = p_expected_writer_epoch
  FOR UPDATE;

  IF NOT FOUND
    OR v_current.state <> 'active'
    OR v_current.capability <> 'cloud_full'
    OR v_current.writer_node_id IS DISTINCT FROM v_stream.writer_node_id
  THEN
    RAISE EXCEPTION 'Cloud is not the active branch writer'
      USING ERRCODE = '55000';
  END IF;

  SELECT sync_node.*
  INTO v_edge
  FROM public.sync_node AS sync_node
  WHERE sync_node.id = p_edge_node_id
  FOR UPDATE;

  IF NOT FOUND
    OR v_edge.tenant_id IS DISTINCT FROM p_tenant_id
    OR v_edge.branch_id IS DISTINCT FROM p_branch_id
    OR v_edge.node_kind <> 'edge'
    OR v_edge.mode <> 'shadow_readonly'
    OR v_edge.status <> 'active'
    OR v_edge.credential_expires_at <= pg_catalog.now()
    OR (
      v_edge.register_id IS NOT NULL
      AND v_edge.register_id IS DISTINCT FROM p_register_id
    )
  THEN
    RAISE EXCEPTION 'Edge node is not eligible for writer preparation'
      USING ERRCODE = '22023';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.register AS register
    WHERE register.id = p_register_id
      AND register.tenant_id = p_tenant_id
      AND register.branch_id = p_branch_id
  ) THEN
    RAISE EXCEPTION 'Register does not belong to the handover branch'
      USING ERRCODE = '22023';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.shift AS shift
    WHERE shift.tenant_id = p_tenant_id
      AND shift.branch_id = p_branch_id
      AND shift.status <> 'closed'
  ) OR EXISTS (
    SELECT 1
    FROM public.sale AS sale
    WHERE sale.tenant_id = p_tenant_id
      AND sale.branch_id = p_branch_id
      AND sale.status = 'draft'
  ) OR EXISTS (
    SELECT 1
    FROM public.incoming_document AS incoming_document
    WHERE incoming_document.tenant_id = p_tenant_id
      AND incoming_document.branch_id = p_branch_id
      AND incoming_document.status = 'draft'
  ) THEN
    RAISE EXCEPTION 'Branch has unfinished operational documents'
      USING ERRCODE = '55000';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_shadow_report AS report
    WHERE report.tenant_id = p_tenant_id
      AND report.branch_id = p_branch_id
      AND report.edge_node_id = p_edge_node_id
      AND report.origin_node_id = v_stream.writer_node_id
      AND report.writer_epoch = v_stream.writer_epoch
      AND report.last_sequence = v_stream.last_sequence
      AND report.source_checksum = v_stream.current_checksum
      AND report.expected_source_checksum = v_stream.current_checksum
      AND report.source_verified
      AND report.projection_checksum = v_stream.current_projection_checksum
      AND report.expected_checksum = v_stream.current_projection_checksum
      AND report.status = 'matched'
  ) THEN
    RAISE EXCEPTION 'Edge has no matching shadow checkpoint at the Cloud tip'
      USING ERRCODE = '55000';
  END IF;

  INSERT INTO public.register_receipt_counter (
    tenant_id,
    branch_id,
    register_id,
    writer_epoch,
    last_receipt_seq
  ) VALUES (
    p_tenant_id,
    p_branch_id,
    p_register_id,
    p_expected_writer_epoch,
    0
  )
  ON CONFLICT (tenant_id, register_id) DO NOTHING;

  SELECT counter.last_receipt_seq
  INTO v_receipt_baseline
  FROM public.register_receipt_counter AS counter
  WHERE counter.tenant_id = p_tenant_id
    AND counter.branch_id = p_branch_id
    AND counter.register_id = p_register_id
  FOR UPDATE;

  IF v_receipt_baseline IS NULL THEN
    RAISE EXCEPTION 'Receipt counter is unavailable for handover'
      USING ERRCODE = '55000';
  END IF;

  v_next_epoch := p_expected_writer_epoch + 1;
  v_manifest_hash := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        'aurum:handover-manifest:v1:'
        || p_activation_id::TEXT || ':'
        || p_tenant_id::TEXT || ':'
        || p_branch_id::TEXT || ':'
        || p_edge_node_id::TEXT || ':'
        || p_register_id::TEXT || ':'
        || v_next_epoch::TEXT || ':'
        || p_expected_writer_epoch::TEXT || ':'
        || p_expected_sequence::TEXT || ':'
        || p_expected_source_checksum || ':'
        || p_expected_projection_checksum || ':'
        || p_bootstrap_snapshot_hash || ':'
        || v_receipt_baseline::TEXT,
        'UTF8'
      )
    ),
    'hex'
  );
  v_root_source_checksum := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        'aurum:writer-root:source:v1:'
        || p_expected_source_checksum || ':'
        || p_bootstrap_snapshot_hash || ':'
        || v_manifest_hash,
        'UTF8'
      )
    ),
    'hex'
  );
  v_root_projection_checksum := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        'aurum:writer-root:projection:v1:'
        || p_expected_projection_checksum || ':'
        || p_bootstrap_snapshot_hash || ':'
        || v_manifest_hash,
        'UTF8'
      )
    ),
    'hex'
  );

  INSERT INTO public.sync_writer_activation (
    tenant_id,
    branch_id,
    writer_epoch,
    activation_id,
    writer_node_id,
    allowed_register_id,
    capability,
    state,
    root_source_checksum,
    root_projection_checksum,
    last_sequence,
    current_source_checksum,
    current_projection_checksum,
    previous_writer_epoch,
    previous_terminal_sequence,
    previous_terminal_source_checksum,
    previous_terminal_projection_checksum,
    bootstrap_snapshot_hash,
    activation_manifest_hash,
    receipt_baseline_seq,
    prepare_request_hash,
    prepared_at
  ) VALUES (
    p_tenant_id,
    p_branch_id,
    v_next_epoch,
    p_activation_id,
    p_edge_node_id,
    p_register_id,
    'cash_sale_v1',
    'prepared',
    v_root_source_checksum,
    v_root_projection_checksum,
    0,
    v_root_source_checksum,
    v_root_projection_checksum,
    p_expected_writer_epoch,
    p_expected_sequence,
    p_expected_source_checksum,
    p_expected_projection_checksum,
    p_bootstrap_snapshot_hash,
    v_manifest_hash,
    v_receipt_baseline,
    p_request_hash,
    pg_catalog.now()
  );

  UPDATE public.sync_node AS sync_node
  SET register_id = p_register_id
  WHERE sync_node.id = p_edge_node_id;

  RETURN p_activation_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RECORD_READINESS_SQL = """
CREATE FUNCTION public.record_edge_writer_readiness(
  p_activation_id UUID,
  p_writer_epoch BIGINT,
  p_previous_sequence BIGINT,
  p_previous_source_checksum TEXT,
  p_previous_projection_checksum TEXT,
  p_bootstrap_snapshot_hash TEXT,
  p_activation_manifest_hash TEXT,
  p_receipt_baseline_seq BIGINT,
  p_request_hash TEXT
) RETURNS UUID AS $$
DECLARE
  v_tenant_id UUID;
  v_branch_id UUID;
  v_edge_node_id UUID;
  v_epoch public.sync_writer_activation%ROWTYPE;
  v_stream public.sync_stream%ROWTYPE;
  v_existing public.sync_writer_readiness%ROWTYPE;
  v_counter BIGINT;
BEGIN
  IF SESSION_USER <> 'aurum_app' THEN
    RAISE EXCEPTION 'Edge readiness requires the runtime database role'
      USING ERRCODE = '42501';
  END IF;

  v_tenant_id := public.current_tenant_id();
  v_branch_id := NULLIF(
    pg_catalog.current_setting('app.branch_id', true),
    ''
  )::UUID;
  v_edge_node_id := NULLIF(
    pg_catalog.current_setting('app.edge_node_id', true),
    ''
  )::UUID;

  IF v_tenant_id IS NULL
    OR v_branch_id IS NULL
    OR v_edge_node_id IS NULL
    OR p_writer_epoch <= 0
    OR p_previous_sequence < 0
    OR p_receipt_baseline_seq < 0
    OR p_previous_source_checksum !~ '^[0-9a-f]{64}$'
    OR p_previous_projection_checksum !~ '^[0-9a-f]{64}$'
    OR p_bootstrap_snapshot_hash !~ '^[0-9a-f]{64}$'
    OR p_activation_manifest_hash !~ '^[0-9a-f]{64}$'
    OR p_request_hash !~ '^[0-9a-f]{64}$'
  THEN
    RAISE EXCEPTION 'Invalid Edge readiness report'
      USING ERRCODE = '22023';
  END IF;

  SELECT sync_stream.*
  INTO v_stream
  FROM public.sync_stream AS sync_stream
  WHERE sync_stream.tenant_id = v_tenant_id
    AND sync_stream.branch_id = v_branch_id
  FOR SHARE;

  SELECT writer_epoch.*
  INTO v_epoch
  FROM public.sync_writer_activation AS writer_epoch
  WHERE writer_epoch.activation_id = p_activation_id
    AND writer_epoch.tenant_id = v_tenant_id
    AND writer_epoch.branch_id = v_branch_id
    AND writer_epoch.writer_epoch = p_writer_epoch
  FOR UPDATE;

  IF NOT FOUND
    OR v_epoch.state NOT IN ('prepared', 'ready')
    OR v_epoch.writer_node_id IS DISTINCT FROM v_edge_node_id
    OR v_epoch.previous_terminal_sequence IS DISTINCT FROM p_previous_sequence
    OR v_epoch.previous_terminal_source_checksum
      IS DISTINCT FROM p_previous_source_checksum
    OR v_epoch.previous_terminal_projection_checksum
      IS DISTINCT FROM p_previous_projection_checksum
    OR v_epoch.bootstrap_snapshot_hash IS DISTINCT FROM p_bootstrap_snapshot_hash
    OR v_epoch.activation_manifest_hash IS DISTINCT FROM p_activation_manifest_hash
    OR v_epoch.receipt_baseline_seq IS DISTINCT FROM p_receipt_baseline_seq
  THEN
    RAISE EXCEPTION 'Readiness does not match the prepared handover'
      USING ERRCODE = '55000';
  END IF;

  IF v_stream.writer_epoch IS DISTINCT FROM v_epoch.previous_writer_epoch
    OR v_stream.last_sequence IS DISTINCT FROM p_previous_sequence
    OR v_stream.current_checksum IS DISTINCT FROM p_previous_source_checksum
    OR v_stream.current_projection_checksum
      IS DISTINCT FROM p_previous_projection_checksum
  THEN
    RAISE EXCEPTION 'Cloud checkpoint changed after preparation'
      USING ERRCODE = '40001';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_node AS sync_node
    WHERE sync_node.id = v_edge_node_id
      AND sync_node.tenant_id = v_tenant_id
      AND sync_node.branch_id = v_branch_id
      AND sync_node.register_id = v_epoch.allowed_register_id
      AND sync_node.node_kind = 'edge'
      AND sync_node.mode = 'shadow_readonly'
      AND sync_node.status = 'active'
      AND sync_node.credential_expires_at > pg_catalog.now()
  ) THEN
    RAISE EXCEPTION 'Prepared Edge node is unavailable'
      USING ERRCODE = '55000';
  END IF;

  SELECT counter.last_receipt_seq
  INTO v_counter
  FROM public.register_receipt_counter AS counter
  WHERE counter.tenant_id = v_tenant_id
    AND counter.branch_id = v_branch_id
    AND counter.register_id = v_epoch.allowed_register_id
  FOR SHARE;

  IF v_counter IS DISTINCT FROM p_receipt_baseline_seq THEN
    RAISE EXCEPTION 'Receipt baseline changed after preparation'
      USING ERRCODE = '40001';
  END IF;

  SELECT readiness.*
  INTO v_existing
  FROM public.sync_writer_readiness AS readiness
  WHERE readiness.activation_id = p_activation_id
  FOR UPDATE;

  IF FOUND THEN
    IF v_existing.tenant_id IS DISTINCT FROM v_tenant_id
      OR v_existing.branch_id IS DISTINCT FROM v_branch_id
      OR v_existing.edge_node_id IS DISTINCT FROM v_edge_node_id
      OR v_existing.register_id IS DISTINCT FROM v_epoch.allowed_register_id
      OR v_existing.writer_epoch IS DISTINCT FROM p_writer_epoch
      OR v_existing.previous_sequence IS DISTINCT FROM p_previous_sequence
      OR v_existing.previous_source_checksum
        IS DISTINCT FROM p_previous_source_checksum
      OR v_existing.previous_projection_checksum
        IS DISTINCT FROM p_previous_projection_checksum
      OR v_existing.bootstrap_snapshot_hash IS DISTINCT FROM p_bootstrap_snapshot_hash
      OR v_existing.activation_manifest_hash IS DISTINCT FROM p_activation_manifest_hash
      OR v_existing.receipt_baseline_seq IS DISTINCT FROM p_receipt_baseline_seq
      OR v_existing.request_hash IS DISTINCT FROM p_request_hash
    THEN
      RAISE EXCEPTION 'Activation readiness was already recorded differently'
        USING ERRCODE = '55000';
    END IF;
    IF v_epoch.state <> 'ready' THEN
      RAISE EXCEPTION 'Activation state does not match persisted readiness'
        USING ERRCODE = '55000';
    END IF;
    RETURN v_existing.activation_id;
  END IF;

  INSERT INTO public.sync_writer_readiness (
    activation_id,
    tenant_id,
    branch_id,
    edge_node_id,
    register_id,
    writer_epoch,
    previous_sequence,
    previous_source_checksum,
    previous_projection_checksum,
    bootstrap_snapshot_hash,
    activation_manifest_hash,
    receipt_baseline_seq,
    request_hash
  ) VALUES (
    p_activation_id,
    v_tenant_id,
    v_branch_id,
    v_edge_node_id,
    v_epoch.allowed_register_id,
    p_writer_epoch,
    p_previous_sequence,
    p_previous_source_checksum,
    p_previous_projection_checksum,
    p_bootstrap_snapshot_hash,
    p_activation_manifest_hash,
    p_receipt_baseline_seq,
    p_request_hash
  );

  UPDATE public.sync_writer_activation AS activation
  SET
    state = 'ready',
    ready_at = pg_catalog.now()
  WHERE activation.activation_id = p_activation_id;

  RETURN p_activation_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ACTIVATE_HANDOVER_SQL = """
CREATE FUNCTION public.activate_edge_writer_handover(
  p_activation_id UUID,
  p_tenant_id UUID,
  p_branch_id UUID,
  p_activation_manifest_hash TEXT
) RETURNS UUID AS $$
DECLARE
  v_stream public.sync_stream%ROWTYPE;
  v_target public.sync_writer_activation%ROWTYPE;
  v_previous public.sync_writer_epoch%ROWTYPE;
  v_readiness public.sync_writer_readiness%ROWTYPE;
  v_counter BIGINT;
BEGIN
  IF SESSION_USER <> 'aurum_support'
    OR COALESCE(
      pg_catalog.current_setting('app.support_session', true),
      'false'
    ) <> 'true'
    OR COALESCE(
      pg_catalog.current_setting('app.edge_writer_activation_enabled', true),
      'false'
    ) <> 'true'
  THEN
    RAISE EXCEPTION 'Edge writer activation is disabled'
      USING ERRCODE = '42501';
  END IF;

  IF p_activation_manifest_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Invalid activation manifest hash'
      USING ERRCODE = '22023';
  END IF;

  SELECT sync_stream.*
  INTO v_stream
  FROM public.sync_stream AS sync_stream
  WHERE sync_stream.tenant_id = p_tenant_id
    AND sync_stream.branch_id = p_branch_id
  FOR UPDATE;

  SELECT writer_epoch.*
  INTO v_target
  FROM public.sync_writer_activation AS writer_epoch
  WHERE writer_epoch.activation_id = p_activation_id
    AND writer_epoch.tenant_id = p_tenant_id
    AND writer_epoch.branch_id = p_branch_id
  FOR UPDATE;

  IF NOT FOUND
    OR v_target.activation_manifest_hash IS DISTINCT FROM p_activation_manifest_hash
  THEN
    RAISE EXCEPTION 'Prepared writer handover was not found'
      USING ERRCODE = '55000';
  END IF;

  IF v_target.state = 'activated'
    AND v_stream.writer_node_id = v_target.writer_node_id
    AND v_stream.writer_epoch = v_target.writer_epoch
  THEN
    RETURN v_target.activation_id;
  END IF;

  IF v_target.state <> 'ready'
    OR v_target.capability <> 'cash_sale_v1'
    OR v_target.allowed_register_id IS NULL
  THEN
    RAISE EXCEPTION 'Writer handover is not prepared for activation'
      USING ERRCODE = '55000';
  END IF;

  SELECT writer_epoch.*
  INTO v_previous
  FROM public.sync_writer_epoch AS writer_epoch
  WHERE writer_epoch.tenant_id = p_tenant_id
    AND writer_epoch.branch_id = p_branch_id
    AND writer_epoch.writer_epoch = v_target.previous_writer_epoch
  FOR UPDATE;

  IF NOT FOUND
    OR v_previous.state <> 'active'
    OR v_previous.capability <> 'cloud_full'
    OR v_stream.writer_node_id IS DISTINCT FROM v_previous.writer_node_id
    OR v_stream.writer_epoch IS DISTINCT FROM v_previous.writer_epoch
    OR v_stream.last_sequence IS DISTINCT FROM v_target.previous_terminal_sequence
    OR v_stream.current_checksum
      IS DISTINCT FROM v_target.previous_terminal_source_checksum
    OR v_stream.current_projection_checksum
      IS DISTINCT FROM v_target.previous_terminal_projection_checksum
  THEN
    RAISE EXCEPTION 'Cloud writer changed after preparation'
      USING ERRCODE = '40001';
  END IF;

  SELECT readiness.*
  INTO v_readiness
  FROM public.sync_writer_readiness AS readiness
  WHERE readiness.activation_id = p_activation_id
    AND readiness.tenant_id = p_tenant_id
    AND readiness.branch_id = p_branch_id
    AND readiness.writer_epoch = v_target.writer_epoch
  FOR UPDATE;

  IF NOT FOUND
    OR v_readiness.edge_node_id IS DISTINCT FROM v_target.writer_node_id
    OR v_readiness.register_id IS DISTINCT FROM v_target.allowed_register_id
    OR v_readiness.previous_sequence
      IS DISTINCT FROM v_target.previous_terminal_sequence
    OR v_readiness.previous_source_checksum
      IS DISTINCT FROM v_target.previous_terminal_source_checksum
    OR v_readiness.previous_projection_checksum
      IS DISTINCT FROM v_target.previous_terminal_projection_checksum
    OR v_readiness.bootstrap_snapshot_hash
      IS DISTINCT FROM v_target.bootstrap_snapshot_hash
    OR v_readiness.activation_manifest_hash
      IS DISTINCT FROM v_target.activation_manifest_hash
    OR v_readiness.receipt_baseline_seq IS DISTINCT FROM v_target.receipt_baseline_seq
  THEN
    RAISE EXCEPTION 'Edge readiness does not match the prepared handover'
      USING ERRCODE = '55000';
  END IF;

  SELECT counter.last_receipt_seq
  INTO v_counter
  FROM public.register_receipt_counter AS counter
  WHERE counter.tenant_id = p_tenant_id
    AND counter.branch_id = p_branch_id
    AND counter.register_id = v_target.allowed_register_id
  FOR UPDATE;

  IF v_counter IS DISTINCT FROM v_target.receipt_baseline_seq THEN
    RAISE EXCEPTION 'Receipt baseline changed before activation'
      USING ERRCODE = '40001';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.shift AS shift
    WHERE shift.tenant_id = p_tenant_id
      AND shift.branch_id = p_branch_id
      AND shift.status <> 'closed'
  ) OR EXISTS (
    SELECT 1
    FROM public.sale AS sale
    WHERE sale.tenant_id = p_tenant_id
      AND sale.branch_id = p_branch_id
      AND sale.status = 'draft'
  ) OR EXISTS (
    SELECT 1
    FROM public.incoming_document AS incoming_document
    WHERE incoming_document.tenant_id = p_tenant_id
      AND incoming_document.branch_id = p_branch_id
      AND incoming_document.status = 'draft'
  ) THEN
    RAISE EXCEPTION 'Branch has unfinished operational documents'
      USING ERRCODE = '55000';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_node AS sync_node
    WHERE sync_node.id = v_target.writer_node_id
      AND sync_node.tenant_id = p_tenant_id
      AND sync_node.branch_id = p_branch_id
      AND sync_node.register_id = v_target.allowed_register_id
      AND sync_node.node_kind = 'edge'
      AND sync_node.mode = 'shadow_readonly'
      AND sync_node.status = 'active'
      AND sync_node.credential_expires_at > pg_catalog.now()
  ) THEN
    RAISE EXCEPTION 'Prepared Edge node is unavailable'
      USING ERRCODE = '55000';
  END IF;

  UPDATE public.sync_writer_epoch AS writer_epoch
  SET
    state = 'fenced',
    fenced_at = pg_catalog.now()
  WHERE writer_epoch.tenant_id = p_tenant_id
    AND writer_epoch.branch_id = p_branch_id
    AND writer_epoch.writer_epoch = v_previous.writer_epoch;

  INSERT INTO public.sync_writer_epoch (
    tenant_id,
    branch_id,
    writer_epoch,
    activation_id,
    writer_node_id,
    allowed_register_id,
    capability,
    state,
    root_source_checksum,
    root_projection_checksum,
    last_sequence,
    current_source_checksum,
    current_projection_checksum,
    previous_writer_epoch,
    previous_terminal_sequence,
    previous_terminal_source_checksum,
    previous_terminal_projection_checksum,
    bootstrap_snapshot_hash,
    activation_manifest_hash,
    receipt_baseline_seq,
    prepared_at,
    activated_at
  ) VALUES (
    v_target.tenant_id,
    v_target.branch_id,
    v_target.writer_epoch,
    v_target.activation_id,
    v_target.writer_node_id,
    v_target.allowed_register_id,
    v_target.capability,
    'active',
    v_target.root_source_checksum,
    v_target.root_projection_checksum,
    0,
    v_target.root_source_checksum,
    v_target.root_projection_checksum,
    v_target.previous_writer_epoch,
    v_target.previous_terminal_sequence,
    v_target.previous_terminal_source_checksum,
    v_target.previous_terminal_projection_checksum,
    v_target.bootstrap_snapshot_hash,
    v_target.activation_manifest_hash,
    v_target.receipt_baseline_seq,
    v_target.prepared_at,
    pg_catalog.now()
  );

  UPDATE public.sync_node AS sync_node
  SET
    mode = 'edge_writer',
    register_id = v_target.allowed_register_id
  WHERE sync_node.id = v_target.writer_node_id;

  UPDATE public.sync_stream AS sync_stream
  SET
    writer_node_id = v_target.writer_node_id,
    writer_epoch = v_target.writer_epoch,
    last_sequence = 0,
    current_checksum = v_target.root_source_checksum,
    current_projection_checksum = v_target.root_projection_checksum
  WHERE sync_stream.id = v_stream.id;

  UPDATE public.register_receipt_counter AS counter
  SET writer_epoch = v_target.writer_epoch
  WHERE counter.tenant_id = p_tenant_id
    AND counter.branch_id = p_branch_id
    AND counter.register_id = v_target.allowed_register_id;

  UPDATE public.sync_writer_activation AS activation
  SET
    state = 'activated',
    activated_at = pg_catalog.now()
  WHERE activation.activation_id = p_activation_id;

  RETURN p_activation_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CANCEL_HANDOVER_SQL = """
CREATE FUNCTION public.cancel_edge_writer_handover(
  p_activation_id UUID,
  p_tenant_id UUID,
  p_branch_id UUID,
  p_activation_manifest_hash TEXT
) RETURNS UUID AS $$
DECLARE
  v_stream public.sync_stream%ROWTYPE;
  v_target public.sync_writer_activation%ROWTYPE;
BEGIN
  IF SESSION_USER <> 'aurum_support'
    OR COALESCE(
      pg_catalog.current_setting('app.support_session', true),
      'false'
    ) <> 'true'
  THEN
    RAISE EXCEPTION 'Writer handover cancellation requires a support session'
      USING ERRCODE = '42501';
  END IF;

  SELECT sync_stream.*
  INTO v_stream
  FROM public.sync_stream AS sync_stream
  WHERE sync_stream.tenant_id = p_tenant_id
    AND sync_stream.branch_id = p_branch_id
  FOR UPDATE;

  SELECT activation.*
  INTO v_target
  FROM public.sync_writer_activation AS activation
  WHERE activation.activation_id = p_activation_id
    AND activation.tenant_id = p_tenant_id
    AND activation.branch_id = p_branch_id
  FOR UPDATE;

  IF NOT FOUND
    OR v_target.activation_manifest_hash IS DISTINCT FROM p_activation_manifest_hash
  THEN
    RAISE EXCEPTION 'Prepared writer handover was not found'
      USING ERRCODE = '55000';
  END IF;

  IF v_target.state = 'aborted' THEN
    RETURN p_activation_id;
  END IF;

  IF v_target.state NOT IN ('prepared', 'ready')
    OR v_stream.writer_epoch IS DISTINCT FROM v_target.previous_writer_epoch
    OR v_stream.last_sequence IS DISTINCT FROM v_target.previous_terminal_sequence
    OR v_stream.current_checksum
      IS DISTINCT FROM v_target.previous_terminal_source_checksum
    OR v_stream.current_projection_checksum
      IS DISTINCT FROM v_target.previous_terminal_projection_checksum
  THEN
    RAISE EXCEPTION 'Only an unchanged pending handover can be cancelled'
      USING ERRCODE = '55000';
  END IF;

  UPDATE public.sync_writer_activation AS activation
  SET
    state = 'aborted',
    aborted_at = pg_catalog.now()
  WHERE activation.activation_id = p_activation_id;

  UPDATE public.sync_node AS sync_node
  SET register_id = NULL
  WHERE sync_node.id = v_target.writer_node_id
    AND sync_node.mode = 'shadow_readonly'
    AND NOT EXISTS (
      SELECT 1
      FROM public.sync_writer_activation AS other_activation
      WHERE other_activation.writer_node_id = sync_node.id
        AND other_activation.activation_id <> p_activation_id
        AND other_activation.state IN ('prepared', 'ready')
    )
    AND NOT EXISTS (
      SELECT 1
      FROM public.sync_writer_epoch AS active_epoch
      WHERE active_epoch.writer_node_id = sync_node.id
        AND active_epoch.state = 'active'
    );

  RETURN p_activation_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ACTIVATION_TRANSITION_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sync_writer_activation() RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF SESSION_USER = 'aurum_support'
      AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
    THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Writer activation history is immutable'
      USING ERRCODE = '55000';
  END IF;

  IF ROW(
    NEW.activation_id,
    NEW.tenant_id,
    NEW.branch_id,
    NEW.writer_epoch,
    NEW.writer_node_id,
    NEW.allowed_register_id,
    NEW.capability,
    NEW.root_source_checksum,
    NEW.root_projection_checksum,
    NEW.last_sequence,
    NEW.current_source_checksum,
    NEW.current_projection_checksum,
    NEW.previous_writer_epoch,
    NEW.previous_terminal_sequence,
    NEW.previous_terminal_source_checksum,
    NEW.previous_terminal_projection_checksum,
    NEW.bootstrap_snapshot_hash,
    NEW.activation_manifest_hash,
    NEW.receipt_baseline_seq,
    NEW.prepare_request_hash,
    NEW.prepared_at,
    NEW.created_at,
    NEW.created_by
  ) IS DISTINCT FROM ROW(
    OLD.activation_id,
    OLD.tenant_id,
    OLD.branch_id,
    OLD.writer_epoch,
    OLD.writer_node_id,
    OLD.allowed_register_id,
    OLD.capability,
    OLD.root_source_checksum,
    OLD.root_projection_checksum,
    OLD.last_sequence,
    OLD.current_source_checksum,
    OLD.current_projection_checksum,
    OLD.previous_writer_epoch,
    OLD.previous_terminal_sequence,
    OLD.previous_terminal_source_checksum,
    OLD.previous_terminal_projection_checksum,
    OLD.bootstrap_snapshot_hash,
    OLD.activation_manifest_hash,
    OLD.receipt_baseline_seq,
    OLD.prepare_request_hash,
    OLD.prepared_at,
    OLD.created_at,
    OLD.created_by
  ) THEN
    RAISE EXCEPTION 'Writer activation identity is immutable'
      USING ERRCODE = '55000';
  END IF;

  IF NOT (
    (OLD.state = 'prepared' AND NEW.state IN ('ready', 'aborted'))
    OR (OLD.state = 'ready' AND NEW.state IN ('activated', 'aborted'))
  ) THEN
    RAISE EXCEPTION 'Invalid writer activation state transition'
      USING ERRCODE = '55000';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


READINESS_IMMUTABILITY_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sync_writer_readiness() RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE'
    AND SESSION_USER = 'aurum_support'
    AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
  THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'Writer readiness history is immutable'
    USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


EPOCH_IMMUTABILITY_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sync_writer_epoch() RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF SESSION_USER = 'aurum_support'
      AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
    THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Writer epoch history is immutable'
      USING ERRCODE = '55000';
  END IF;

  IF ROW(
    NEW.tenant_id,
    NEW.branch_id,
    NEW.writer_epoch,
    NEW.activation_id,
    NEW.writer_node_id,
    NEW.allowed_register_id,
    NEW.capability,
    NEW.root_source_checksum,
    NEW.root_projection_checksum,
    NEW.previous_writer_epoch,
    NEW.previous_terminal_sequence,
    NEW.previous_terminal_source_checksum,
    NEW.previous_terminal_projection_checksum,
    NEW.bootstrap_snapshot_hash,
    NEW.activation_manifest_hash,
    NEW.receipt_baseline_seq,
    NEW.prepared_at,
    NEW.activated_at,
    NEW.created_at,
    NEW.created_by
  ) IS DISTINCT FROM ROW(
    OLD.tenant_id,
    OLD.branch_id,
    OLD.writer_epoch,
    OLD.activation_id,
    OLD.writer_node_id,
    OLD.allowed_register_id,
    OLD.capability,
    OLD.root_source_checksum,
    OLD.root_projection_checksum,
    OLD.previous_writer_epoch,
    OLD.previous_terminal_sequence,
    OLD.previous_terminal_source_checksum,
    OLD.previous_terminal_projection_checksum,
    OLD.bootstrap_snapshot_hash,
    OLD.activation_manifest_hash,
    OLD.receipt_baseline_seq,
    OLD.prepared_at,
    OLD.activated_at,
    OLD.created_at,
    OLD.created_by
  ) THEN
    RAISE EXCEPTION 'Writer epoch identity is immutable'
      USING ERRCODE = '55000';
  END IF;

  IF OLD.state = 'active' AND NEW.state = 'active' THEN
    IF NEW.fenced_at IS NOT NULL OR NEW.last_sequence < OLD.last_sequence THEN
      RAISE EXCEPTION 'Invalid active writer epoch update'
        USING ERRCODE = '55000';
    END IF;
  ELSIF OLD.state = 'active' AND NEW.state = 'fenced' THEN
    IF NEW.fenced_at IS NULL
      OR NEW.last_sequence IS DISTINCT FROM OLD.last_sequence
      OR NEW.current_source_checksum IS DISTINCT FROM OLD.current_source_checksum
      OR NEW.current_projection_checksum
        IS DISTINCT FROM OLD.current_projection_checksum
    THEN
      RAISE EXCEPTION 'Invalid writer epoch fencing'
        USING ERRCODE = '55000';
    END IF;
  ELSE
    RAISE EXCEPTION 'Invalid writer epoch state transition'
      USING ERRCODE = '55000';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_SCOPE_GUARD_SQL = """
CREATE OR REPLACE FUNCTION public.trg_guard_sync_stream_scope() RETURNS TRIGGER AS $$
DECLARE
  v_branch_id UUID;
BEGIN
  IF SESSION_USER = 'aurum_app' THEN
    v_branch_id := NULLIF(
      pg_catalog.current_setting('app.branch_id', true),
      ''
    )::UUID;

    IF public.current_tenant_id() IS NULL
      OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
      OR v_branch_id IS NULL
      OR NEW.branch_id IS DISTINCT FROM v_branch_id
      OR NULLIF(
        pg_catalog.current_setting('app.edge_node_id', true),
        ''
      ) IS NOT NULL
    THEN
      RAISE EXCEPTION 'Sync stream writes require a Cloud branch scope'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_RESERVE_POSITION_SQL = """
CREATE OR REPLACE FUNCTION public.reserve_sync_event_position(
  p_tenant_id UUID,
  p_branch_id UUID
) RETURNS TABLE(
  stream_id UUID,
  origin_node_id UUID,
  writer_epoch BIGINT,
  sequence BIGINT,
  previous_checksum TEXT,
  previous_projection_checksum TEXT
) AS $$
DECLARE
  v_stream_id UUID;
  v_origin_node_id UUID;
  v_writer_epoch BIGINT;
  v_previous_checksum TEXT;
  v_previous_projection_checksum TEXT;
  v_sequence BIGINT;
BEGIN
  IF p_tenant_id IS NULL
    OR p_branch_id IS NULL
    OR (
      p_tenant_id IS DISTINCT FROM public.current_tenant_id()
      AND SESSION_USER <> 'aurum_support'
    )
  THEN
    RAISE EXCEPTION 'Invalid sync stream scope'
      USING ERRCODE = '42501';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.branch AS branch
    WHERE branch.id = p_branch_id
      AND branch.tenant_id = p_tenant_id
  ) THEN
    RAISE EXCEPTION 'Sync stream branch not found'
      USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.sync_node (
    tenant_id,
    branch_id,
    node_kind,
    mode,
    status,
    display_name
  ) VALUES (
    p_tenant_id,
    p_branch_id,
    'cloud',
    'cloud_writer',
    'active',
    'Cloud writer'
  )
  ON CONFLICT (tenant_id, branch_id)
    WHERE node_kind = 'cloud'
  DO NOTHING;

  SELECT sync_node.id
  INTO v_origin_node_id
  FROM public.sync_node AS sync_node
  WHERE sync_node.tenant_id = p_tenant_id
    AND sync_node.branch_id = p_branch_id
    AND sync_node.node_kind = 'cloud';

  INSERT INTO public.sync_stream (
    tenant_id,
    branch_id,
    writer_node_id,
    writer_epoch,
    last_sequence,
    current_checksum,
    current_projection_checksum
  ) VALUES (
    p_tenant_id,
    p_branch_id,
    v_origin_node_id,
    1,
    0,
    repeat('0', 64),
    repeat('0', 64)
  )
  ON CONFLICT (tenant_id, branch_id) DO NOTHING;

  SELECT
    sync_stream.id,
    sync_stream.writer_node_id,
    sync_stream.writer_epoch,
    sync_stream.current_checksum,
    sync_stream.current_projection_checksum
  INTO
    v_stream_id,
    v_origin_node_id,
    v_writer_epoch,
    v_previous_checksum,
    v_previous_projection_checksum
  FROM public.sync_stream AS sync_stream
  WHERE sync_stream.tenant_id = p_tenant_id
    AND sync_stream.branch_id = p_branch_id
  FOR UPDATE;

  IF v_stream_id IS NULL THEN
    RAISE EXCEPTION 'Sync stream is unavailable'
      USING ERRCODE = '55000';
  END IF;

  UPDATE public.sync_stream AS sync_stream
  SET last_sequence = sync_stream.last_sequence + 1
  WHERE sync_stream.id = v_stream_id
  RETURNING sync_stream.last_sequence INTO v_sequence;

  RETURN QUERY
  SELECT
    v_stream_id,
    v_origin_node_id,
    v_writer_epoch,
    v_sequence,
    v_previous_checksum,
    v_previous_projection_checksum;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_ALLOCATE_RECEIPT_SQL = """
CREATE OR REPLACE FUNCTION public.allocate_register_receipt(
  p_tenant_id UUID,
  p_register_id UUID
) RETURNS TABLE(
  receipt_seq BIGINT,
  receipt_number TEXT
) AS $$
DECLARE
  v_branch_id UUID;
  v_writer_node_id UUID;
  v_writer_epoch BIGINT;
  v_receipt_seq BIGINT;
BEGIN
  SELECT register.branch_id
  INTO v_branch_id
  FROM public.register AS register
  WHERE register.id = p_register_id
    AND register.tenant_id = p_tenant_id
  FOR UPDATE;

  IF v_branch_id IS NULL THEN
    RETURN;
  END IF;

  IF SESSION_USER = 'aurum_app'
    AND (
      public.current_tenant_id() IS DISTINCT FROM p_tenant_id
      OR NULLIF(
        pg_catalog.current_setting('app.branch_id', true),
        ''
      )::UUID IS DISTINCT FROM v_branch_id
      OR NULLIF(
        pg_catalog.current_setting('app.edge_node_id', true),
        ''
      ) IS NOT NULL
    )
  THEN
    RAISE EXCEPTION 'Receipt allocation requires a Cloud branch scope'
      USING ERRCODE = '42501';
  ELSIF SESSION_USER NOT IN ('aurum_app', 'aurum_support') THEN
    RAISE EXCEPTION 'Receipt allocation is not allowed'
      USING ERRCODE = '42501';
  END IF;

  INSERT INTO public.sync_node (
    tenant_id,
    branch_id,
    node_kind,
    mode,
    status,
    display_name
  ) VALUES (
    p_tenant_id,
    v_branch_id,
    'cloud',
    'cloud_writer',
    'active',
    'Cloud writer'
  )
  ON CONFLICT (tenant_id, branch_id)
    WHERE node_kind = 'cloud'
  DO NOTHING;

  SELECT sync_node.id
  INTO v_writer_node_id
  FROM public.sync_node AS sync_node
  WHERE sync_node.tenant_id = p_tenant_id
    AND sync_node.branch_id = v_branch_id
    AND sync_node.node_kind = 'cloud';

  INSERT INTO public.sync_stream (
    tenant_id,
    branch_id,
    writer_node_id,
    writer_epoch,
    last_sequence,
    current_checksum,
    current_projection_checksum
  ) VALUES (
    p_tenant_id,
    v_branch_id,
    v_writer_node_id,
    1,
    0,
    repeat('0', 64),
    repeat('0', 64)
  )
  ON CONFLICT (tenant_id, branch_id) DO NOTHING;

  SELECT sync_stream.writer_epoch
  INTO v_writer_epoch
  FROM public.sync_stream AS sync_stream
  JOIN public.sync_node AS sync_node
    ON sync_node.id = sync_stream.writer_node_id
   AND sync_node.tenant_id = sync_stream.tenant_id
   AND sync_node.branch_id = sync_stream.branch_id
  WHERE sync_stream.tenant_id = p_tenant_id
    AND sync_stream.branch_id = v_branch_id
    AND sync_node.node_kind = 'cloud'
    AND sync_node.mode = 'cloud_writer'
    AND sync_node.status = 'active'
  FOR UPDATE OF sync_stream;

  IF v_writer_epoch IS NULL THEN
    RAISE EXCEPTION 'Cloud receipt writer is not active'
      USING ERRCODE = '55000';
  END IF;

  INSERT INTO public.register_receipt_counter (
    tenant_id,
    branch_id,
    register_id,
    writer_epoch,
    last_receipt_seq
  ) VALUES (
    p_tenant_id,
    v_branch_id,
    p_register_id,
    v_writer_epoch,
    0
  )
  ON CONFLICT (tenant_id, register_id) DO NOTHING;

  UPDATE public.register_receipt_counter AS counter
  SET
    writer_epoch = v_writer_epoch,
    last_receipt_seq = counter.last_receipt_seq + 1
  WHERE counter.tenant_id = p_tenant_id
    AND counter.branch_id = v_branch_id
    AND counter.register_id = p_register_id
  RETURNING counter.last_receipt_seq INTO v_receipt_seq;

  IF v_receipt_seq IS NULL THEN
    RAISE EXCEPTION 'Receipt counter is unavailable'
      USING ERRCODE = '55000';
  END IF;

  RETURN QUERY
  SELECT v_receipt_seq, pg_catalog.lpad(v_receipt_seq::TEXT, 6, '0');
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_AUTHENTICATE_EDGE_SQL = """
CREATE OR REPLACE FUNCTION public.authenticate_edge_node(
  p_credential_kid UUID,
  p_credential_hash TEXT
) RETURNS TABLE(
  node_id UUID,
  tenant_id UUID,
  branch_id UUID,
  shadow_start_sequence BIGINT,
  shadow_start_checksum TEXT,
  shadow_start_projection_checksum TEXT
) AS $$
BEGIN
  IF p_credential_kid IS NULL
    OR p_credential_hash !~ '^[0-9a-f]{64}$'
  THEN
    RETURN;
  END IF;

  RETURN QUERY
  SELECT
    sync_node.id,
    sync_node.tenant_id,
    sync_node.branch_id,
    sync_node.shadow_start_sequence,
    sync_node.shadow_start_checksum,
    sync_node.shadow_start_projection_checksum
  FROM public.sync_node AS sync_node
  WHERE sync_node.node_kind = 'edge'
    AND sync_node.mode = 'shadow_readonly'
    AND sync_node.status = 'active'
    AND sync_node.credential_kid = p_credential_kid
    AND sync_node.credential_hash = p_credential_hash
    AND sync_node.credential_expires_at > pg_catalog.now();

  UPDATE public.sync_node AS sync_node
  SET last_seen_at = pg_catalog.now()
  WHERE sync_node.node_kind = 'edge'
    AND sync_node.mode = 'shadow_readonly'
    AND sync_node.status = 'active'
    AND sync_node.credential_kid = p_credential_kid
    AND sync_node.credential_hash = p_credential_hash
    AND sync_node.credential_expires_at > pg_catalog.now();
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _secure_function(signature: str, *, grant_app: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")
    if grant_app:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_app")


def _add_branch_guard(table: str) -> None:
    op.execute(f"""
        CREATE TRIGGER trg_{table}_writer_guard
        BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_branch_writer()
        """)


def _meta_and_audit_triggers(table: str) -> None:
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
    op.execute(f"""
        CREATE TRIGGER trg_audit_{table}
        AFTER INSERT OR UPDATE OR DELETE ON public.{table}
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)


def _edge_scoped_rls(table: str, node_column: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{table}")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON public.{table}
        USING (
          tenant_id = public.current_tenant_id()
          AND (
            NULLIF(pg_catalog.current_setting('app.edge_node_id', true), '') IS NULL
            OR (
              branch_id = NULLIF(
                pg_catalog.current_setting('app.branch_id', true),
                ''
              )::UUID
              AND {node_column} = NULLIF(
                pg_catalog.current_setting('app.edge_node_id', true),
                ''
              )::UUID
            )
          )
        )
        WITH CHECK (
          tenant_id = public.current_tenant_id()
          AND (
            NULLIF(pg_catalog.current_setting('app.edge_node_id', true), '') IS NULL
            OR (
              branch_id = NULLIF(
                pg_catalog.current_setting('app.branch_id', true),
                ''
              )::UUID
              AND {node_column} = NULLIF(
                pg_catalog.current_setting('app.edge_node_id', true),
                ''
              )::UUID
            )
          )
        )
        """)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT SELECT ON TABLE public.{table} TO aurum_app")


def _create_writer_activation() -> None:
    op.execute("""
        CREATE TABLE public.sync_writer_activation (
          activation_id                  UUID PRIMARY KEY,
          tenant_id                      UUID NOT NULL
                                         REFERENCES public.tenant(id) ON DELETE CASCADE,
          branch_id                      UUID NOT NULL
                                         REFERENCES public.branch(id) ON DELETE RESTRICT,
          writer_epoch                   BIGINT NOT NULL CHECK (writer_epoch > 0),
          writer_node_id                 UUID NOT NULL,
          allowed_register_id            UUID NOT NULL,
          capability                     TEXT NOT NULL DEFAULT 'cash_sale_v1',
          state                          TEXT NOT NULL,
          root_source_checksum           TEXT NOT NULL
                                         CHECK (root_source_checksum ~ '^[0-9a-f]{64}$'),
          root_projection_checksum       TEXT NOT NULL
                                         CHECK (root_projection_checksum ~ '^[0-9a-f]{64}$'),
          last_sequence                  BIGINT NOT NULL DEFAULT 0
                                         CHECK (last_sequence >= 0),
          current_source_checksum        TEXT NOT NULL
                                         CHECK (current_source_checksum ~ '^[0-9a-f]{64}$'),
          current_projection_checksum    TEXT NOT NULL
                                         CHECK (current_projection_checksum ~ '^[0-9a-f]{64}$'),
          previous_writer_epoch          BIGINT NOT NULL,
          previous_terminal_sequence     BIGINT NOT NULL
                                         CHECK (previous_terminal_sequence >= 0),
          previous_terminal_source_checksum TEXT NOT NULL
                                         CHECK (
                                           previous_terminal_source_checksum
                                           ~ '^[0-9a-f]{64}$'
                                         ),
          previous_terminal_projection_checksum TEXT NOT NULL
                                         CHECK (
                                           previous_terminal_projection_checksum
                                           ~ '^[0-9a-f]{64}$'
                                         ),
          bootstrap_snapshot_hash        TEXT NOT NULL
                                         CHECK (bootstrap_snapshot_hash ~ '^[0-9a-f]{64}$'),
          activation_manifest_hash       TEXT NOT NULL
                                         CHECK (activation_manifest_hash ~ '^[0-9a-f]{64}$'),
          receipt_baseline_seq            BIGINT NOT NULL
                                         CHECK (receipt_baseline_seq >= 0),
          prepare_request_hash           TEXT NOT NULL
                                         CHECK (prepare_request_hash ~ '^[0-9a-f]{64}$'),
          prepared_at                    TIMESTAMPTZ NOT NULL,
          ready_at                       TIMESTAMPTZ,
          activated_at                   TIMESTAMPTZ,
          aborted_at                     TIMESTAMPTZ,
          created_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by                     UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by                     UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_sync_writer_activation_scope
            UNIQUE (activation_id, tenant_id, branch_id, writer_epoch),
          CONSTRAINT ck_sync_writer_activation_writer CHECK (
            capability = 'cash_sale_v1' AND allowed_register_id IS NOT NULL
          ),
          CONSTRAINT ck_sync_writer_activation_previous CHECK (
            previous_writer_epoch = writer_epoch - 1
          ),
          CONSTRAINT ck_sync_writer_activation_state CHECK (
            state IN ('prepared', 'ready', 'aborted', 'activated')
          ),
          CONSTRAINT ck_sync_writer_activation_state_time CHECK (
            (
              state = 'prepared'
              AND ready_at IS NULL
              AND activated_at IS NULL
              AND aborted_at IS NULL
            )
            OR (
              state = 'ready'
              AND ready_at IS NOT NULL
              AND activated_at IS NULL
              AND aborted_at IS NULL
            )
            OR (
              state = 'activated'
              AND ready_at IS NOT NULL
              AND activated_at IS NOT NULL
              AND aborted_at IS NULL
            )
            OR (
              state = 'aborted'
              AND activated_at IS NULL
              AND aborted_at IS NOT NULL
            )
          ),
          CONSTRAINT ck_sync_writer_activation_root_position CHECK (
            last_sequence = 0
            AND current_source_checksum = root_source_checksum
            AND current_projection_checksum = root_projection_checksum
          ),
          CONSTRAINT fk_sync_writer_activation_previous
            FOREIGN KEY (tenant_id, branch_id, previous_writer_epoch)
            REFERENCES public.sync_writer_epoch (tenant_id, branch_id, writer_epoch)
            ON DELETE RESTRICT,
          CONSTRAINT fk_sync_writer_activation_node_scope
            FOREIGN KEY (writer_node_id, tenant_id, branch_id)
            REFERENCES public.sync_node (id, tenant_id, branch_id)
            ON DELETE RESTRICT,
          CONSTRAINT fk_sync_writer_activation_register_scope
            FOREIGN KEY (allowed_register_id, tenant_id, branch_id)
            REFERENCES public.register (id, tenant_id, branch_id)
            ON DELETE RESTRICT
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_sync_writer_activation_pending_branch
        ON public.sync_writer_activation (tenant_id, branch_id)
        WHERE state IN ('prepared', 'ready')
        """)
    _meta_and_audit_triggers("sync_writer_activation")
    _edge_scoped_rls("sync_writer_activation", "writer_node_id")


def _install_handover_schema() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM public.sync_writer_epoch WHERE state = 'prepared'
          ) OR EXISTS (
            SELECT 1 FROM public.sync_writer_readiness
          ) THEN
            RAISE EXCEPTION
              '0045 requires no legacy prepared epochs or readiness rows';
          END IF;
        END
        $$
        """)

    op.execute("""
        ALTER TABLE public.sync_writer_epoch
        ADD COLUMN previous_terminal_sequence BIGINT
          CHECK (previous_terminal_sequence IS NULL OR previous_terminal_sequence >= 0)
        """)
    op.execute(
        "ALTER TABLE public.sync_writer_epoch " "DROP CONSTRAINT ck_sync_writer_epoch_previous"
    )
    op.execute("""
        ALTER TABLE public.sync_writer_epoch
        ADD CONSTRAINT ck_sync_writer_epoch_previous CHECK (
          (
            previous_writer_epoch IS NULL
            AND previous_terminal_sequence IS NULL
            AND previous_terminal_source_checksum IS NULL
            AND previous_terminal_projection_checksum IS NULL
          )
          OR (
            previous_writer_epoch = writer_epoch - 1
            AND previous_terminal_sequence >= 0
            AND previous_terminal_source_checksum ~ '^[0-9a-f]{64}$'
            AND previous_terminal_projection_checksum ~ '^[0-9a-f]{64}$'
          )
        )
        """)
    op.execute("DROP INDEX public.uq_sync_writer_epoch_prepared_branch")
    op.execute("ALTER TABLE public.sync_writer_epoch DROP CONSTRAINT sync_writer_epoch_state_check")
    op.execute("""
        ALTER TABLE public.sync_writer_epoch
        ADD CONSTRAINT sync_writer_epoch_state_check
        CHECK (state IN ('active', 'fenced'))
        """)

    op.execute("""
        ALTER TABLE public.sync_node
        ADD CONSTRAINT uq_sync_node_id_tenant_branch
          UNIQUE (id, tenant_id, branch_id)
        """)
    op.execute("""
        ALTER TABLE public.register
        ADD CONSTRAINT uq_register_id_tenant_branch
          UNIQUE (id, tenant_id, branch_id)
        """)
    op.execute("""
        ALTER TABLE public.sync_node
        ADD CONSTRAINT fk_sync_node_register_scope
          FOREIGN KEY (register_id, tenant_id, branch_id)
          REFERENCES public.register (id, tenant_id, branch_id)
          ON DELETE RESTRICT
        """)
    op.execute("""
        ALTER TABLE public.sync_writer_epoch
        ADD CONSTRAINT uq_sync_writer_epoch_owner_scope
          UNIQUE (tenant_id, branch_id, writer_epoch, writer_node_id),
        ADD CONSTRAINT fk_sync_writer_epoch_node_scope
          FOREIGN KEY (writer_node_id, tenant_id, branch_id)
          REFERENCES public.sync_node (id, tenant_id, branch_id)
          ON DELETE RESTRICT,
        ADD CONSTRAINT fk_sync_writer_epoch_register_scope
          FOREIGN KEY (allowed_register_id, tenant_id, branch_id)
          REFERENCES public.register (id, tenant_id, branch_id)
          ON DELETE RESTRICT
        """)
    op.execute("""
        ALTER TABLE public.register_receipt_counter
        ADD CONSTRAINT fk_register_receipt_counter_register_scope
          FOREIGN KEY (register_id, tenant_id, branch_id)
          REFERENCES public.register (id, tenant_id, branch_id)
          ON DELETE RESTRICT
        """)
    op.execute("ALTER TABLE public.sync_stream DROP CONSTRAINT fk_sync_stream_writer_epoch")
    op.execute("""
        ALTER TABLE public.sync_stream
        ADD CONSTRAINT fk_sync_stream_writer_epoch
          FOREIGN KEY (tenant_id, branch_id, writer_epoch, writer_node_id)
          REFERENCES public.sync_writer_epoch (
            tenant_id,
            branch_id,
            writer_epoch,
            writer_node_id
          )
          DEFERRABLE INITIALLY DEFERRED
        """)

    _create_writer_activation()
    op.execute(
        "ALTER TABLE public.sync_writer_readiness DROP CONSTRAINT fk_sync_writer_readiness_epoch"
    )
    op.execute(
        "ALTER TABLE public.sync_writer_readiness DROP CONSTRAINT uq_sync_writer_readiness_scope"
    )
    op.execute("""
        ALTER TABLE public.sync_writer_readiness
        ADD CONSTRAINT fk_sync_writer_readiness_activation
          FOREIGN KEY (activation_id, tenant_id, branch_id, writer_epoch)
          REFERENCES public.sync_writer_activation (
            activation_id,
            tenant_id,
            branch_id,
            writer_epoch
          )
          ON DELETE RESTRICT,
        ADD CONSTRAINT fk_sync_writer_readiness_node_scope
          FOREIGN KEY (edge_node_id, tenant_id, branch_id)
          REFERENCES public.sync_node (id, tenant_id, branch_id)
          ON DELETE RESTRICT,
        ADD CONSTRAINT fk_sync_writer_readiness_register_scope
          FOREIGN KEY (register_id, tenant_id, branch_id)
          REFERENCES public.register (id, tenant_id, branch_id)
          ON DELETE RESTRICT
        """)
    _edge_scoped_rls("sync_writer_readiness", "edge_node_id")

    op.execute(ACTIVATION_TRANSITION_GUARD_SQL)
    _secure_function("public.trg_guard_sync_writer_activation()")
    op.execute("""
        CREATE TRIGGER trg_guard_sync_writer_activation
        BEFORE UPDATE OR DELETE ON public.sync_writer_activation
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_writer_activation()
        """)
    op.execute(READINESS_IMMUTABILITY_GUARD_SQL)
    _secure_function("public.trg_guard_sync_writer_readiness()")
    op.execute("""
        CREATE TRIGGER trg_guard_sync_writer_readiness
        BEFORE UPDATE OR DELETE ON public.sync_writer_readiness
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_writer_readiness()
        """)
    op.execute(EPOCH_IMMUTABILITY_GUARD_SQL)
    _secure_function("public.trg_guard_sync_writer_epoch()")
    op.execute("""
        CREATE TRIGGER trg_guard_sync_writer_epoch
        BEFORE UPDATE OR DELETE ON public.sync_writer_epoch
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_writer_epoch()
        """)


def upgrade() -> None:
    _install_handover_schema()

    op.execute(INITIALIZE_BRANCH_WRITER_SQL)
    _secure_function("public.initialize_branch_sync_writer(UUID, UUID)")
    op.execute(INITIALIZE_BRANCH_TRIGGER_SQL)
    _secure_function("public.trg_initialize_branch_sync_writer()")
    op.execute("""
        CREATE TRIGGER trg_branch_sync_writer
        AFTER INSERT ON public.branch
        FOR EACH ROW EXECUTE FUNCTION public.trg_initialize_branch_sync_writer()
        """)
    op.execute("""
        SELECT public.initialize_branch_sync_writer(branch.tenant_id, branch.id)
        FROM public.branch AS branch
        """)

    op.execute(ASSERT_BRANCH_WRITER_SQL)
    _secure_function("public.assert_current_branch_writer(UUID, UUID, UUID, BOOLEAN)")
    op.execute(BRANCH_WRITE_GUARD_SQL)
    _secure_function("public.trg_guard_branch_writer()")
    for table in ("shift", "sale", "batch", "write_off", "incoming_document"):
        _add_branch_guard(table)

    op.execute(OUTBOX_WRITE_GUARD_SQL)
    _secure_function("public.trg_guard_sync_outbox_writer()")
    op.execute("""
        CREATE TRIGGER trg_sync_outbox_writer_guard
        BEFORE INSERT OR UPDATE OR DELETE ON public.sync_outbox
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_outbox_writer()
        """)

    op.execute(SYNC_STREAM_SCOPE_GUARD_SQL)
    op.execute(RESERVE_SYNC_EVENT_POSITION_SQL)
    op.execute(ALLOCATE_REGISTER_RECEIPT_SQL)
    op.execute(AUTHENTICATE_EDGE_NODE_SQL)

    op.execute(PREPARE_HANDOVER_SQL)
    _secure_function(
        "public.prepare_edge_writer_handover("
        "UUID, UUID, UUID, UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT)"
    )
    op.execute(RECORD_READINESS_SQL)
    _secure_function(
        "public.record_edge_writer_readiness("
        "UUID, BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, TEXT)",
        grant_app=True,
    )
    op.execute(ACTIVATE_HANDOVER_SQL)
    _secure_function("public.activate_edge_writer_handover(UUID, UUID, UUID, TEXT)")
    op.execute(CANCEL_HANDOVER_SQL)
    _secure_function("public.cancel_edge_writer_handover(UUID, UUID, UUID, TEXT)")


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.sync_writer_epoch
            WHERE capability = 'cash_sale_v1'
          ) OR EXISTS (
            SELECT 1 FROM public.sync_writer_activation
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade 0045 while Edge handover history exists';
          END IF;
        END
        $$
        """)

    op.execute("DROP FUNCTION public.cancel_edge_writer_handover(UUID, UUID, UUID, TEXT)")
    op.execute("DROP FUNCTION public.activate_edge_writer_handover(UUID, UUID, UUID, TEXT)")
    op.execute(
        "DROP FUNCTION public.record_edge_writer_readiness("
        "UUID, BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, TEXT)"
    )
    op.execute(
        "DROP FUNCTION public.prepare_edge_writer_handover("
        "UUID, UUID, UUID, UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT)"
    )

    op.execute("DROP TRIGGER trg_sync_outbox_writer_guard ON public.sync_outbox")
    op.execute("DROP FUNCTION public.trg_guard_sync_outbox_writer()")
    for table in ("incoming_document", "write_off", "batch", "sale", "shift"):
        op.execute(f"DROP TRIGGER trg_{table}_writer_guard ON public.{table}")
    op.execute("DROP FUNCTION public.trg_guard_branch_writer()")

    op.execute("DROP TRIGGER trg_branch_sync_writer ON public.branch")
    op.execute("DROP FUNCTION public.trg_initialize_branch_sync_writer()")

    # Restore the 0044 runtime functions before dropping their new dependency.
    op.execute(LEGACY_SCOPE_GUARD_SQL)
    op.execute(LEGACY_RESERVE_POSITION_SQL)
    op.execute(LEGACY_ALLOCATE_RECEIPT_SQL)
    op.execute(LEGACY_AUTHENTICATE_EDGE_SQL)

    op.execute("DROP FUNCTION public.assert_current_branch_writer(UUID, UUID, UUID, BOOLEAN)")
    op.execute("DROP FUNCTION public.initialize_branch_sync_writer(UUID, UUID)")

    op.execute("DROP TRIGGER trg_guard_sync_writer_epoch ON public.sync_writer_epoch")
    op.execute("DROP FUNCTION public.trg_guard_sync_writer_epoch()")
    op.execute("DROP TRIGGER trg_guard_sync_writer_readiness ON public.sync_writer_readiness")
    op.execute("DROP FUNCTION public.trg_guard_sync_writer_readiness()")
    op.execute("DROP TRIGGER trg_guard_sync_writer_activation " "ON public.sync_writer_activation")
    op.execute("DROP FUNCTION public.trg_guard_sync_writer_activation()")

    op.execute("""
        ALTER TABLE public.sync_writer_readiness
        DROP CONSTRAINT fk_sync_writer_readiness_activation,
        DROP CONSTRAINT fk_sync_writer_readiness_node_scope,
        DROP CONSTRAINT fk_sync_writer_readiness_register_scope,
        ADD CONSTRAINT uq_sync_writer_readiness_scope
          UNIQUE (tenant_id, branch_id, writer_epoch),
        ADD CONSTRAINT fk_sync_writer_readiness_epoch
          FOREIGN KEY (activation_id, tenant_id, branch_id, writer_epoch)
          REFERENCES public.sync_writer_epoch (
            activation_id,
            tenant_id,
            branch_id,
            writer_epoch
          )
          ON DELETE RESTRICT
        """)
    op.execute("DROP POLICY tenant_isolation ON public.sync_writer_readiness")
    op.execute("""
        CREATE POLICY tenant_isolation ON public.sync_writer_readiness
        USING (
          tenant_id = public.current_tenant_id()
          AND (
            NULLIF(pg_catalog.current_setting('app.edge_node_id', true), '') IS NULL
            OR branch_id = NULLIF(
              pg_catalog.current_setting('app.branch_id', true),
              ''
            )::UUID
          )
        )
        WITH CHECK (
          tenant_id = public.current_tenant_id()
          AND (
            NULLIF(pg_catalog.current_setting('app.edge_node_id', true), '') IS NULL
            OR branch_id = NULLIF(
              pg_catalog.current_setting('app.branch_id', true),
              ''
            )::UUID
          )
        )
        """)
    op.execute("DROP TABLE public.sync_writer_activation")

    op.execute("ALTER TABLE public.sync_stream DROP CONSTRAINT fk_sync_stream_writer_epoch")
    op.execute("""
        ALTER TABLE public.sync_stream
        ADD CONSTRAINT fk_sync_stream_writer_epoch
          FOREIGN KEY (tenant_id, branch_id, writer_epoch)
          REFERENCES public.sync_writer_epoch (tenant_id, branch_id, writer_epoch)
          DEFERRABLE INITIALLY DEFERRED
        """)
    op.execute("""
        ALTER TABLE public.register_receipt_counter
        DROP CONSTRAINT fk_register_receipt_counter_register_scope
        """)
    op.execute("""
        ALTER TABLE public.sync_writer_epoch
        DROP CONSTRAINT fk_sync_writer_epoch_node_scope,
        DROP CONSTRAINT fk_sync_writer_epoch_register_scope,
        DROP CONSTRAINT uq_sync_writer_epoch_owner_scope
        """)
    op.execute("ALTER TABLE public.sync_node DROP CONSTRAINT fk_sync_node_register_scope")
    op.execute("ALTER TABLE public.sync_node DROP CONSTRAINT uq_sync_node_id_tenant_branch")
    op.execute("ALTER TABLE public.register DROP CONSTRAINT uq_register_id_tenant_branch")

    op.execute("ALTER TABLE public.sync_writer_epoch DROP CONSTRAINT sync_writer_epoch_state_check")
    op.execute("""
        ALTER TABLE public.sync_writer_epoch
        ADD CONSTRAINT sync_writer_epoch_state_check
        CHECK (state IN ('prepared', 'active', 'fenced'))
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_sync_writer_epoch_prepared_branch
        ON public.sync_writer_epoch (tenant_id, branch_id)
        WHERE state = 'prepared'
        """)
    op.execute(
        "ALTER TABLE public.sync_writer_epoch " "DROP CONSTRAINT ck_sync_writer_epoch_previous"
    )
    op.execute("""
        ALTER TABLE public.sync_writer_epoch
        ADD CONSTRAINT ck_sync_writer_epoch_previous CHECK (
          (
            previous_writer_epoch IS NULL
            AND previous_terminal_source_checksum IS NULL
            AND previous_terminal_projection_checksum IS NULL
          )
          OR (
            previous_writer_epoch = writer_epoch - 1
            AND previous_terminal_source_checksum ~ '^[0-9a-f]{64}$'
            AND previous_terminal_projection_checksum ~ '^[0-9a-f]{64}$'
          )
        )
        """)
    op.execute("ALTER TABLE public.sync_writer_epoch DROP COLUMN previous_terminal_sequence")
