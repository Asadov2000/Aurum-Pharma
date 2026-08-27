"""add the fail-closed Edge cash sale database dispatcher

Revision ID: 0113
Revises: 0112
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0113"
down_revision: str | Sequence[str] | None = "0112"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DISPATCH_SIGNATURE = "public.dispatch_edge_cash_sale_v1(UUID, UUID, BIGINT, UUID, JSONB, TEXT)"


CANONICAL_JSON_SQL = r"""
CREATE FUNCTION public.edge_canonical_jsonb(p_value JSONB)
RETURNS TEXT
LANGUAGE plpgsql
IMMUTABLE
STRICT
PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
  v_result TEXT;
BEGIN
  CASE pg_catalog.jsonb_typeof(p_value)
    WHEN 'object' THEN
      SELECT '{' || COALESCE(
        pg_catalog.string_agg(
          pg_catalog.to_jsonb(entry.key)::TEXT || ':'
            || public.edge_canonical_jsonb(entry.value),
          ',' ORDER BY entry.key
        ),
        ''
      ) || '}'
      INTO v_result
      FROM pg_catalog.jsonb_each(p_value) AS entry(key, value);
    WHEN 'array' THEN
      SELECT '[' || COALESCE(
        pg_catalog.string_agg(
          public.edge_canonical_jsonb(entry.value),
          ',' ORDER BY entry.ordinality
        ),
        ''
      ) || ']'
      INTO v_result
      FROM pg_catalog.jsonb_array_elements(p_value)
        WITH ORDINALITY AS entry(value, ordinality);
    ELSE
      v_result := p_value::TEXT;
  END CASE;
  RETURN v_result;
END;
$function$
"""


NORMALIZE_NUMERIC_SQL = r"""
CREATE FUNCTION public.edge_normalize_numeric(p_value NUMERIC)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $function$
  SELECT CASE
    WHEN pg_catalog.strpos(p_value::TEXT, '.') = 0 THEN p_value::TEXT
    ELSE COALESCE(
      NULLIF(
        pg_catalog.rtrim(pg_catalog.rtrim(p_value::TEXT, '0'), '.'),
        '-0'
      ),
      '0'
    )
  END
$function$
"""


HARDENED_BATCH_QTY_TRIGGER_SQL = r"""
CREATE OR REPLACE FUNCTION public.trg_update_batch_qty()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
  v_qty_remaining NUMERIC(14,3);
BEGIN
  UPDATE public.batch AS batch
  SET qty_remaining = batch.qty_remaining + NEW.qty_delta
  WHERE batch.id = NEW.batch_id
  RETURNING batch.qty_remaining INTO v_qty_remaining;
  IF v_qty_remaining IS NULL THEN
    RAISE EXCEPTION 'Batch does not exist'
      USING ERRCODE = '23503';
  END IF;
  IF v_qty_remaining < 0 THEN
    RAISE EXCEPTION 'Batch qty_remaining cannot be negative'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$function$
"""


LEGACY_BATCH_QTY_TRIGGER_SQL = r"""
CREATE OR REPLACE FUNCTION public.trg_update_batch_qty()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
BEGIN
  UPDATE batch
  SET qty_remaining = qty_remaining + NEW.qty_delta
  WHERE id = NEW.batch_id;
  IF (SELECT qty_remaining FROM batch WHERE id = NEW.batch_id) < 0 THEN
    RAISE EXCEPTION 'Batch qty_remaining cannot be negative';
  END IF;
  RETURN NEW;
END;
$function$
"""


LEGACY_ASSERT_BRANCH_WRITER_SQL = r"""
CREATE OR REPLACE FUNCTION public.assert_current_branch_writer(
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


ASSERT_BRANCH_WRITER_SQL = r"""
CREATE OR REPLACE FUNCTION public.assert_current_branch_writer(
  p_tenant_id UUID,
  p_branch_id UUID,
  p_register_id UUID,
  p_allow_edge BOOLEAN
) RETURNS VOID AS $$
DECLARE
  v_edge_node_id UUID;
  v_edge_tenant_id UUID;
  v_edge_branch_id UUID;
  v_edge_register_id UUID;
  v_is_edge_session BOOLEAN := false;
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
      AND NULLIF(pg_catalog.current_setting('app.branch_id', true), '')::UUID
        IS DISTINCT FROM p_branch_id
    THEN
      RAISE EXCEPTION 'Branch writer branch scope does not match'
        USING ERRCODE = '42501';
    END IF;
  ELSIF SESSION_USER ~ '^aurum_edge_node_[0-9a-f]{32}$' THEN
    SELECT
      identity.edge_node_id,
      identity.tenant_id,
      identity.branch_id,
      identity.register_id
    INTO
      v_edge_node_id,
      v_edge_tenant_id,
      v_edge_branch_id,
      v_edge_register_id
    FROM public.edge_cash_node_identity AS identity
    JOIN pg_catalog.pg_roles AS role
      ON role.rolname = identity.database_role
     AND role.oid = identity.database_role_oid
    WHERE identity.database_role = SESSION_USER
      AND role.rolcanlogin
      AND role.rolinherit
      AND NOT role.rolsuper
      AND NOT role.rolcreatedb
      AND NOT role.rolcreaterole
      AND NOT role.rolreplication
      AND NOT role.rolbypassrls
      AND (
        SELECT pg_catalog.count(*)
        FROM pg_catalog.pg_auth_members AS membership
        WHERE membership.member = role.oid
      ) = 1
      AND EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members AS membership
        JOIN pg_catalog.pg_roles AS granted
          ON granted.oid = membership.roleid
        WHERE membership.member = role.oid
          AND granted.rolname = 'aurum_edge_cash_executor'
          AND NOT membership.admin_option
          AND membership.inherit_option
          AND NOT membership.set_option
      );

    IF v_edge_node_id IS NULL
      OR v_edge_tenant_id IS DISTINCT FROM p_tenant_id
      OR v_edge_branch_id IS DISTINCT FROM p_branch_id
      OR (
        p_register_id IS NOT NULL
        AND v_edge_register_id IS DISTINCT FROM p_register_id
      )
    THEN
      RAISE EXCEPTION 'Edge database identity does not match branch scope'
        USING ERRCODE = '42501';
    END IF;
    v_is_edge_session := true;
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

  IF NOT v_is_edge_session THEN
    v_edge_node_id := NULLIF(
      pg_catalog.current_setting('app.edge_node_id', true),
      ''
    )::UUID;
  END IF;

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


DISPATCHER_SQL = r"""
CREATE FUNCTION public.dispatch_edge_cash_sale_v1(
  p_operation_id UUID,
  p_activation_id UUID,
  p_writer_epoch BIGINT,
  p_cashier_user_id UUID,
  p_payload JSONB,
  p_request_hash TEXT
) RETURNS JSONB
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $function$
DECLARE
  v_identity public.edge_cash_node_identity%ROWTYPE;
  v_existing public.edge_cash_command%ROWTYPE;
  v_stream public.sync_stream%ROWTYPE;
  v_node public.sync_node%ROWTYPE;
  v_activation public.sync_writer_activation%ROWTYPE;
  v_epoch public.sync_writer_epoch%ROWTYPE;
  v_shift_id UUID;
  v_sale_id UUID := pg_catalog.gen_random_uuid();
  v_payment_id UUID := pg_catalog.gen_random_uuid();
  v_event_id UUID := pg_catalog.gen_random_uuid();
  v_now TIMESTAMPTZ := pg_catalog.clock_timestamp();
  v_timestamp TEXT;
  v_local_today DATE;
  v_timezone TEXT;
  v_paid_amount NUMERIC(14,2);
  v_total_amount NUMERIC(14,2) := 0;
  v_position INTEGER := 0;
  v_requested RECORD;
  v_batch RECORD;
  v_remaining NUMERIC(14,3);
  v_take NUMERIC(14,3);
  v_unit_price NUMERIC(14,2);
  v_line_total NUMERIC(14,2);
  v_normalized_items JSONB;
  v_request JSONB;
  v_computed_request_hash TEXT;
  v_cloud_request JSONB;
  v_cloud_operation_hash TEXT;
  v_receipt_seq BIGINT;
  v_receipt_number TEXT;
  v_receipt_snapshot JSONB;
  v_event_payload JSONB;
  v_event_payload_hash TEXT;
  v_projection_hash TEXT;
  v_stream_checksum TEXT;
  v_projection_checksum TEXT;
  v_sequence BIGINT;
  v_result JSONB;
  v_result_hash TEXT;
BEGIN
  IF p_operation_id IS NULL
    OR (pg_catalog.get_byte(pg_catalog.uuid_send(p_operation_id), 6) >> 4) <> 4
    OR (pg_catalog.get_byte(pg_catalog.uuid_send(p_operation_id), 8) & 192) <> 128
    OR p_activation_id IS NULL
    OR p_writer_epoch IS NULL
    OR p_writer_epoch <= 0
    OR p_cashier_user_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
  THEN
    RAISE EXCEPTION 'Invalid Edge cash command envelope'
      USING ERRCODE = '22023';
  END IF;

  IF pg_catalog.jsonb_typeof(p_payload) <> 'object'
    OR (
      SELECT pg_catalog.count(*)
      FROM pg_catalog.jsonb_object_keys(p_payload)
    ) <> 2
    OR NOT p_payload ? 'items'
    OR NOT p_payload ? 'paid_amount'
    OR pg_catalog.jsonb_typeof(p_payload -> 'items') <> 'array'
    OR pg_catalog.jsonb_array_length(p_payload -> 'items') NOT BETWEEN 1 AND 200
    OR (p_payload ->> 'paid_amount')
      !~ '^(0|[1-9][0-9]{0,11})[.][0-9]{2}$'
  THEN
    RAISE EXCEPTION 'Invalid Edge cash command payload'
      USING ERRCODE = '22023';
  END IF;

  v_paid_amount := (p_payload ->> 'paid_amount')::NUMERIC(14,2);
  IF v_paid_amount <= 0 THEN
    RAISE EXCEPTION 'Cash payment must be positive'
      USING ERRCODE = '22023';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_catalog.jsonb_array_elements(p_payload -> 'items') AS item(value)
    WHERE pg_catalog.jsonb_typeof(item.value) <> 'object'
      OR (
        SELECT pg_catalog.count(*)
        FROM pg_catalog.jsonb_object_keys(item.value)
      ) <> 2
      OR NOT item.value ? 'catalog_id'
      OR NOT item.value ? 'qty'
      OR (item.value ->> 'catalog_id')
        !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      OR (item.value ->> 'qty')
        !~ '^(0|[1-9][0-9]{0,10})([.][0-9]{1,3})?$'
      OR (item.value ->> 'qty')::NUMERIC <= 0
  ) THEN
    RAISE EXCEPTION 'Invalid Edge cash item payload'
      USING ERRCODE = '22023';
  END IF;

  SELECT pg_catalog.jsonb_agg(
           pg_catalog.jsonb_build_array(
             normalized.catalog_id::TEXT,
             public.edge_normalize_numeric(normalized.qty)
           )
           ORDER BY normalized.catalog_id::TEXT
         )
  INTO v_normalized_items
  FROM (
    SELECT
      (item.value ->> 'catalog_id')::UUID AS catalog_id,
      pg_catalog.sum((item.value ->> 'qty')::NUMERIC(14,3)) AS qty
    FROM pg_catalog.jsonb_array_elements(p_payload -> 'items') AS item(value)
    GROUP BY (item.value ->> 'catalog_id')::UUID
  ) AS normalized;

  IF EXISTS (
    SELECT 1
    FROM (
      SELECT pg_catalog.sum((item.value ->> 'qty')::NUMERIC) AS qty
      FROM pg_catalog.jsonb_array_elements(p_payload -> 'items') AS item(value)
      GROUP BY (item.value ->> 'catalog_id')::UUID
    ) AS totals
    WHERE totals.qty > 99999999999.999
  ) THEN
    RAISE EXCEPTION 'Edge cash item quantity is too large'
      USING ERRCODE = '22003';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 0)
  );

  SELECT identity.*
  INTO v_identity
  FROM public.edge_cash_node_identity AS identity
  JOIN pg_catalog.pg_roles AS role
    ON role.rolname = identity.database_role
   AND role.oid = identity.database_role_oid
  WHERE identity.database_role = SESSION_USER
    AND identity.database_role = 'aurum_edge_node_'
      || pg_catalog.replace(identity.edge_node_id::TEXT, '-', '')
    AND role.rolcanlogin
    AND role.rolinherit
    AND role.rolconnlimit = 1
    AND NOT role.rolsuper
    AND NOT role.rolcreatedb
    AND NOT role.rolcreaterole
    AND NOT role.rolreplication
    AND NOT role.rolbypassrls
    AND (
      SELECT pg_catalog.count(*)
      FROM pg_catalog.pg_auth_members AS membership
      WHERE membership.member = role.oid
    ) = 1
    AND EXISTS (
      SELECT 1
      FROM pg_catalog.pg_auth_members AS membership
      JOIN pg_catalog.pg_roles AS granted
        ON granted.oid = membership.roleid
      WHERE membership.member = role.oid
        AND granted.rolname = 'aurum_edge_cash_executor'
        AND NOT membership.admin_option
        AND membership.inherit_option
        AND NOT membership.set_option
    );

  IF v_identity.id IS NULL THEN
    RAISE EXCEPTION 'Edge database identity does not match'
      USING ERRCODE = '42501';
  END IF;

  PERFORM pg_catalog.set_config('app.tenant_id', v_identity.tenant_id::TEXT, true);
  PERFORM pg_catalog.set_config('app.branch_id', v_identity.branch_id::TEXT, true);
  PERFORM pg_catalog.set_config('app.user_id', p_cashier_user_id::TEXT, true);
  PERFORM pg_catalog.set_config('app.edge_node_id', v_identity.edge_node_id::TEXT, true);

  v_request := pg_catalog.jsonb_build_object(
    'activation_id', p_activation_id::TEXT,
    'cashier_user_id', p_cashier_user_id::TEXT,
    'command_type', 'sale.cash.complete',
    'items', v_normalized_items,
    'paid_amount', pg_catalog.to_char(v_paid_amount, 'FM9999999999990.00'),
    'writer_epoch', p_writer_epoch
  );
  v_computed_request_hash := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(public.edge_canonical_jsonb(v_request), 'UTF8')
    ),
    'hex'
  );
  IF v_computed_request_hash IS DISTINCT FROM p_request_hash THEN
    RAISE EXCEPTION 'Edge cash request hash does not match'
      USING ERRCODE = '22023';
  END IF;

  SELECT command.*
  INTO v_existing
  FROM public.edge_cash_command AS command
  WHERE command.tenant_id = v_identity.tenant_id
    AND command.operation_id = p_operation_id;

  IF v_existing.id IS NOT NULL THEN
    IF v_existing.request_hash IS DISTINCT FROM p_request_hash
      OR v_existing.edge_identity_id IS DISTINCT FROM v_identity.id
      OR v_existing.activation_id IS DISTINCT FROM p_activation_id
      OR v_existing.writer_epoch IS DISTINCT FROM p_writer_epoch
      OR v_existing.cashier_user_id IS DISTINCT FROM p_cashier_user_id
      OR v_existing.result_hash IS DISTINCT FROM pg_catalog.encode(
        pg_catalog.sha256(
          pg_catalog.convert_to(
            public.edge_canonical_jsonb(v_existing.result_payload),
            'UTF8'
          )
        ),
        'hex'
      )
    THEN
      RAISE EXCEPTION 'Operation ID was already used for another Edge command'
        USING ERRCODE = '23505';
    END IF;
    RETURN v_existing.result_payload;
  END IF;

  IF EXISTS (
    SELECT 1 FROM public.pos_command
    WHERE tenant_id = v_identity.tenant_id AND operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.pos_payment_attempt
    WHERE tenant_id = v_identity.tenant_id AND operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.pos_refund_attempt
    WHERE tenant_id = v_identity.tenant_id AND operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.sale_payment
    WHERE tenant_id = v_identity.tenant_id AND operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.sale
    WHERE tenant_id = v_identity.tenant_id AND operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.sync_outbox
    WHERE tenant_id = v_identity.tenant_id AND operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Operation ID was already used for another POS operation'
      USING ERRCODE = '23505';
  END IF;

  SELECT stream.*
  INTO v_stream
  FROM public.sync_stream AS stream
  WHERE stream.tenant_id = v_identity.tenant_id
    AND stream.branch_id = v_identity.branch_id
  FOR UPDATE;

  SELECT node.*
  INTO v_node
  FROM public.sync_node AS node
  WHERE node.id = v_identity.edge_node_id
    AND node.tenant_id = v_identity.tenant_id
    AND node.branch_id = v_identity.branch_id
    AND node.register_id = v_identity.register_id
  FOR UPDATE;

  SELECT activation.*
  INTO v_activation
  FROM public.sync_writer_activation AS activation
  WHERE activation.activation_id = p_activation_id
    AND activation.tenant_id = v_identity.tenant_id
    AND activation.branch_id = v_identity.branch_id
    AND activation.writer_epoch = p_writer_epoch
    AND activation.writer_node_id = v_identity.edge_node_id
    AND activation.allowed_register_id = v_identity.register_id
  FOR UPDATE;

  SELECT epoch.*
  INTO v_epoch
  FROM public.sync_writer_epoch AS epoch
  WHERE epoch.activation_id = p_activation_id
    AND epoch.tenant_id = v_identity.tenant_id
    AND epoch.branch_id = v_identity.branch_id
    AND epoch.writer_epoch = p_writer_epoch
    AND epoch.writer_node_id = v_identity.edge_node_id
    AND epoch.allowed_register_id = v_identity.register_id
  FOR UPDATE;

  IF v_stream.id IS NULL
    OR v_node.id IS NULL
    OR v_activation.activation_id IS NULL
    OR v_epoch.activation_id IS NULL
    OR v_stream.writer_node_id IS DISTINCT FROM v_identity.edge_node_id
    OR v_stream.writer_epoch IS DISTINCT FROM p_writer_epoch
    OR v_node.status <> 'active'
    OR v_node.node_kind <> 'edge'
    OR v_node.mode <> 'edge_writer'
    OR v_activation.state <> 'activated'
    OR v_activation.capability <> 'cash_sale_v1'
    OR v_epoch.state <> 'active'
    OR v_epoch.capability <> 'cash_sale_v1'
  THEN
    RAISE EXCEPTION 'Active Edge cash writer scope is unavailable'
      USING ERRCODE = '55000';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.sync_writer_activation AS pending
    WHERE pending.tenant_id = v_identity.tenant_id
      AND pending.branch_id = v_identity.branch_id
      AND pending.state IN ('prepared', 'ready')
  ) THEN
    RAISE EXCEPTION 'Branch writer handover is in progress'
      USING ERRCODE = '55000';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.tenant AS tenant
    WHERE tenant.id = v_identity.tenant_id
      AND tenant.status IN ('trial', 'active')
  ) OR NOT EXISTS (
    SELECT 1
    FROM public.register AS register
    JOIN public.branch AS branch
      ON branch.id = register.branch_id
     AND branch.tenant_id = register.tenant_id
    WHERE register.id = v_identity.register_id
      AND register.tenant_id = v_identity.tenant_id
      AND register.branch_id = v_identity.branch_id
      AND register.is_active
      AND branch.is_active
  ) THEN
    RAISE EXCEPTION 'Pharmacy or register is unavailable for Edge cash sale'
      USING ERRCODE = '55000';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.app_user AS app_user
    JOIN public.tenant_membership AS membership
      ON membership.user_id = app_user.id
     AND membership.tenant_id = v_identity.tenant_id
     AND membership.status = 'active'
    JOIN public.user_assignment AS assignment
      ON assignment.membership_id = membership.id
     AND assignment.user_id = app_user.id
     AND assignment.tenant_id = membership.tenant_id
     AND assignment.is_active
     AND (
       assignment.branch_id IS NULL
       OR assignment.branch_id = v_identity.branch_id
     )
    JOIN public.role AS assigned_role
      ON assigned_role.id = assignment.role_id
     AND assigned_role.is_active
    JOIN public.role_permission AS role_permission
      ON role_permission.role_id = assigned_role.id
     AND role_permission.permission_code = 'pos.sell'
    JOIN public.permission AS permission
      ON permission.code = role_permission.permission_code
     AND permission.is_active
    WHERE app_user.id = p_cashier_user_id
      AND app_user.status = 'active'
  ) THEN
    RAISE EXCEPTION 'Cashier is not authorized for Edge cash sale'
      USING ERRCODE = '42501';
  END IF;

  SELECT shift.id
  INTO v_shift_id
  FROM public.shift AS shift
  WHERE shift.tenant_id = v_identity.tenant_id
    AND shift.branch_id = v_identity.branch_id
    AND shift.register_id = v_identity.register_id
    AND shift.opened_by_user_id = p_cashier_user_id
    AND shift.status = 'open'
  FOR UPDATE;

  IF v_shift_id IS NULL THEN
    RAISE EXCEPTION 'Cashier has no open shift for this register'
      USING ERRCODE = '55000';
  END IF;

  SELECT COALESCE(settings.report_timezone, 'Asia/Dushanbe')
  INTO v_timezone
  FROM public.tenant_settings AS settings
  WHERE settings.tenant_id = v_identity.tenant_id
    AND settings.pos_payment_methods ? 'cash';
  IF v_timezone IS NULL THEN
    RAISE EXCEPTION 'Cash payments are disabled for this pharmacy'
      USING ERRCODE = '55000';
  END IF;
  v_local_today := (v_now AT TIME ZONE v_timezone)::DATE;

  IF EXISTS (
    SELECT 1
    FROM (
      SELECT (item.value ->> 'catalog_id')::UUID AS catalog_id
      FROM pg_catalog.jsonb_array_elements(p_payload -> 'items') AS item(value)
      GROUP BY (item.value ->> 'catalog_id')::UUID
    ) AS requested
    LEFT JOIN public.tenant_catalog AS catalog
      ON catalog.id = requested.catalog_id
     AND catalog.tenant_id = v_identity.tenant_id
     AND catalog.deleted_at IS NULL
     AND catalog.is_active
     AND catalog.dispensing_type = 'otc'
    WHERE catalog.id IS NULL
  ) THEN
    RAISE EXCEPTION 'Edge cash sale contains an unavailable catalog item'
      USING ERRCODE = '22023';
  END IF;

  PERFORM batch.id
  FROM public.batch AS batch
  WHERE batch.tenant_id = v_identity.tenant_id
    AND batch.branch_id = v_identity.branch_id
    AND batch.catalog_id IN (
      SELECT (item.value ->> 'catalog_id')::UUID
      FROM pg_catalog.jsonb_array_elements(p_payload -> 'items') AS item(value)
    )
    AND batch.qty_remaining > 0
    AND NOT batch.is_blocked
    AND batch.expires_at > v_local_today
  ORDER BY batch.id
  FOR UPDATE;

  v_cloud_request := pg_catalog.jsonb_build_object(
    'draft_sale_id', NULL,
    'items', v_normalized_items,
    'kind', 'sale_checkout_v1',
    'payments', pg_catalog.jsonb_build_array(
      pg_catalog.jsonb_build_object(
        'amount', public.edge_normalize_numeric(v_paid_amount),
        'metadata', NULL,
        'payment_method', 'cash'
      )
    ),
    'prescription', NULL,
    'register_id', v_identity.register_id::TEXT
  );
  v_cloud_operation_hash := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        public.edge_canonical_jsonb(v_cloud_request),
        'UTF8'
      )
    ),
    'hex'
  );

  INSERT INTO public.sale (
    id, tenant_id, branch_id, register_id, shift_id, sale_type,
    status, is_test, total_amount, currency, cashier_user_id,
    created_at, operation_id, operation_hash
  ) VALUES (
    v_sale_id, v_identity.tenant_id, v_identity.branch_id,
    v_identity.register_id, v_shift_id, 'sale', 'draft', false, 0,
    'TJS', p_cashier_user_id, v_now, p_operation_id,
    v_cloud_operation_hash
  );

  FOR v_requested IN
    SELECT
      (item.value ->> 'catalog_id')::UUID AS catalog_id,
      pg_catalog.sum((item.value ->> 'qty')::NUMERIC(14,3)) AS qty
    FROM pg_catalog.jsonb_array_elements(p_payload -> 'items') AS item(value)
    GROUP BY (item.value ->> 'catalog_id')::UUID
    ORDER BY (item.value ->> 'catalog_id')::UUID::TEXT
  LOOP
    v_remaining := v_requested.qty;
    FOR v_batch IN
      SELECT
        batch.id,
        batch.qty_remaining,
        COALESCE(NULLIF(batch.sale_price, 0), catalog.base_price, 0)::NUMERIC(14,2)
          AS unit_price
      FROM public.batch AS batch
      JOIN public.tenant_catalog AS catalog
        ON catalog.id = batch.catalog_id
       AND catalog.tenant_id = batch.tenant_id
      WHERE batch.tenant_id = v_identity.tenant_id
        AND batch.branch_id = v_identity.branch_id
        AND batch.catalog_id = v_requested.catalog_id
        AND batch.qty_remaining > 0
        AND NOT batch.is_blocked
        AND batch.expires_at > v_local_today
      ORDER BY batch.expires_at, batch.created_at, batch.id
    LOOP
      EXIT WHEN v_remaining <= 0;
      v_take := LEAST(v_batch.qty_remaining, v_remaining);
      v_unit_price := v_batch.unit_price;
      IF v_unit_price <= 0 THEN
        RAISE EXCEPTION 'Catalog item has no valid sale price'
          USING ERRCODE = '22023';
      END IF;
      v_line_total := pg_catalog.round(v_unit_price * v_take, 2);
      v_position := v_position + 1;
      INSERT INTO public.sale_item (
        id, tenant_id, sale_id, catalog_id, batch_id, qty,
        unit_price, total_price, currency, discount_amount,
        position, created_at
      ) VALUES (
        pg_catalog.gen_random_uuid(), v_identity.tenant_id, v_sale_id,
        v_requested.catalog_id, v_batch.id, v_take, v_unit_price,
        v_line_total, 'TJS', 0, v_position, v_now
      );
      v_total_amount := v_total_amount + v_line_total;
      v_remaining := v_remaining - v_take;
    END LOOP;
    IF v_remaining > 0 THEN
      RAISE EXCEPTION 'Insufficient stock for Edge cash sale'
        USING ERRCODE = '22023';
    END IF;
  END LOOP;

  IF v_total_amount <= 0 OR v_total_amount IS DISTINCT FROM v_paid_amount THEN
    RAISE EXCEPTION 'Cash payment total does not match sale total'
      USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.sale_payment (
    id, tenant_id, sale_id, payment_method, amount, currency, created_at
  ) VALUES (
    v_payment_id, v_identity.tenant_id, v_sale_id, 'cash',
    v_total_amount, 'TJS', v_now
  );

  INSERT INTO public.batch_movement (
    id, tenant_id, batch_id, movement_type, qty_delta,
    source_table, source_id, created_at, created_by, operation_key
  )
  SELECT
    pg_catalog.gen_random_uuid(), item.tenant_id, item.batch_id, 'sale',
    -pg_catalog.sum(item.qty), 'sale', v_sale_id, v_now,
    p_cashier_user_id,
    'pos:sale:' || v_sale_id::TEXT || ':sale:' || item.batch_id::TEXT
  FROM public.sale_item AS item
  WHERE item.sale_id = v_sale_id
  GROUP BY item.tenant_id, item.batch_id;

  INSERT INTO public.register_receipt_counter (
    tenant_id, branch_id, register_id, writer_epoch,
    last_receipt_seq, created_at, created_by, updated_at, updated_by
  ) VALUES (
    v_identity.tenant_id, v_identity.branch_id, v_identity.register_id,
    p_writer_epoch, 0, v_now, p_cashier_user_id, v_now, p_cashier_user_id
  ) ON CONFLICT (tenant_id, register_id) DO NOTHING;

  UPDATE public.register_receipt_counter AS counter
  SET
    writer_epoch = p_writer_epoch,
    last_receipt_seq = counter.last_receipt_seq + 1,
    updated_at = v_now,
    updated_by = p_cashier_user_id
  WHERE counter.tenant_id = v_identity.tenant_id
    AND counter.branch_id = v_identity.branch_id
    AND counter.register_id = v_identity.register_id
  RETURNING counter.last_receipt_seq INTO v_receipt_seq;
  IF v_receipt_seq IS NULL THEN
    RAISE EXCEPTION 'Receipt counter is unavailable'
      USING ERRCODE = '55000';
  END IF;
  v_receipt_number := pg_catalog.lpad(v_receipt_seq::TEXT, 6, '0');
  v_timestamp := pg_catalog.to_char(
    v_now AT TIME ZONE 'UTC',
    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
  );

  SELECT pg_catalog.jsonb_build_object(
    'branch_address', branch.address,
    'branch_license', branch.license_number,
    'branch_name', branch.name,
    'cashier_name', app_user.full_name,
    'change', '0.00',
    'currency', 'TJS',
    'datetime', v_timestamp,
    'discount_total', '0.00',
    'is_refund', false,
    'items', (
      SELECT pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'discount_amount', pg_catalog.to_char(item.discount_amount, 'FM9999999999990.00'),
          'name', catalog.brand_name,
          'position', item.position,
          'qty', public.edge_normalize_numeric(item.qty),
          'total_price', pg_catalog.to_char(item.total_price, 'FM9999999999990.00'),
          'unit_price', pg_catalog.to_char(item.unit_price, 'FM9999999999990.00')
        ) ORDER BY item.position
      )
      FROM public.sale_item AS item
      JOIN public.tenant_catalog AS catalog ON catalog.id = item.catalog_id
      WHERE item.sale_id = v_sale_id
    ),
    'paid_total', pg_catalog.to_char(v_total_amount, 'FM9999999999990.00'),
    'payments', pg_catalog.jsonb_build_array(
      pg_catalog.jsonb_build_object(
        'amount', pg_catalog.to_char(v_total_amount, 'FM9999999999990.00'),
        'method', 'cash'
      )
    ),
    'pharmacy_name', tenant.name,
    'receipt_number', v_receipt_number,
    'sale_id', v_sale_id::TEXT,
    'status', 'completed',
    'total', pg_catalog.to_char(v_total_amount, 'FM9999999999990.00')
  )
  INTO v_receipt_snapshot
  FROM public.tenant AS tenant
  JOIN public.branch AS branch
    ON branch.id = v_identity.branch_id
   AND branch.tenant_id = tenant.id
  JOIN public.app_user AS app_user ON app_user.id = p_cashier_user_id
  WHERE tenant.id = v_identity.tenant_id;

  UPDATE public.sale AS sale
  SET
    total_amount = v_total_amount,
    status = 'completed',
    completed_at = v_now,
    receipt_number = v_receipt_number,
    receipt_seq = v_receipt_seq,
    receipt_snapshot = v_receipt_snapshot
  WHERE sale.id = v_sale_id;

  SELECT pg_catalog.jsonb_build_object(
    'branch_id', v_identity.branch_id::TEXT,
    'cashier_user_id', p_cashier_user_id::TEXT,
    'completed_at', v_timestamp,
    'created_at', v_timestamp,
    'currency', 'TJS',
    'event_id', v_event_id::TEXT,
    'is_test', false,
    'items', (
      SELECT pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'batch_id', item.batch_id::TEXT,
          'catalog_id', item.catalog_id::TEXT,
          'currency', item.currency,
          'discount_amount', pg_catalog.to_char(item.discount_amount, 'FM9999999999990.00'),
          'id', item.id::TEXT,
          'position', item.position,
          'qty', public.edge_normalize_numeric(item.qty),
          'total_price', pg_catalog.to_char(item.total_price, 'FM9999999999990.00'),
          'unit_price', pg_catalog.to_char(item.unit_price, 'FM9999999999990.00')
        ) ORDER BY item.position
      )
      FROM public.sale_item AS item
      WHERE item.sale_id = v_sale_id
    ),
    'operation_id', p_operation_id::TEXT,
    'payments', pg_catalog.jsonb_build_array(
      pg_catalog.jsonb_build_object(
        'amount', pg_catalog.to_char(v_total_amount, 'FM9999999999990.00'),
        'currency', 'TJS',
        'id', v_payment_id::TEXT,
        'payment_attempt_id', NULL,
        'payment_attempt_status', NULL,
        'payment_method', 'cash'
      )
    ),
    'receipt_number', v_receipt_number,
    'receipt_seq', v_receipt_seq,
    'register_id', v_identity.register_id::TEXT,
    'sale_id', v_sale_id::TEXT,
    'shift_id', v_shift_id::TEXT,
    'tenant_id', v_identity.tenant_id::TEXT,
    'total_amount', pg_catalog.to_char(v_total_amount, 'FM9999999999990.00')
  ) INTO v_event_payload;

  v_event_payload_hash := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(public.edge_canonical_jsonb(v_event_payload), 'UTF8')
    ),
    'hex'
  );
  v_projection_hash := v_event_payload_hash;
  v_sequence := v_stream.last_sequence + 1;
  v_stream_checksum := pg_catalog.encode(
    pg_catalog.sha256(pg_catalog.convert_to(public.edge_canonical_jsonb(
      pg_catalog.jsonb_build_object(
        'aggregate_id', v_sale_id::TEXT,
        'aggregate_type', 'sale',
        'branch_id', v_identity.branch_id::TEXT,
        'event_id', v_event_id::TEXT,
        'event_type', 'pos.sale.completed.v1',
        'occurred_at', v_timestamp,
        'operation_id', p_operation_id::TEXT,
        'origin_node_id', v_identity.edge_node_id::TEXT,
        'payload_hash', v_event_payload_hash,
        'previous_checksum', v_stream.current_checksum,
        'schema_version', 1,
        'sequence', v_sequence,
        'tenant_id', v_identity.tenant_id::TEXT,
        'writer_epoch', p_writer_epoch
      )
    ), 'UTF8')), 'hex'
  );
  v_projection_checksum := pg_catalog.encode(
    pg_catalog.sha256(pg_catalog.convert_to(public.edge_canonical_jsonb(
      pg_catalog.jsonb_build_object(
        'origin_node_id', v_identity.edge_node_id::TEXT,
        'previous_checksum', v_stream.current_projection_checksum,
        'projection_hash', v_projection_hash,
        'sale_id', v_sale_id::TEXT,
        'sequence', v_sequence,
        'writer_epoch', p_writer_epoch
      )
    ), 'UTF8')), 'hex'
  );

  UPDATE public.sync_stream AS stream
  SET
    last_sequence = v_sequence,
    current_checksum = v_stream_checksum,
    current_projection_checksum = v_projection_checksum,
    updated_at = v_now,
    updated_by = p_cashier_user_id
  WHERE stream.id = v_stream.id
    AND stream.last_sequence = v_stream.last_sequence;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Sync stream checkpoint conflict'
      USING ERRCODE = '40001';
  END IF;

  INSERT INTO public.sync_outbox (
    event_id, tenant_id, branch_id, operation_id, aggregate_type,
    aggregate_id, event_type, schema_version, payload, payload_hash,
    delivery_status, attempts, available_at, created_at, created_by,
    updated_at, updated_by, origin_node_id, writer_epoch, sequence,
    occurred_at, stream_checksum, projection_hash, projection_checksum
  ) VALUES (
    v_event_id, v_identity.tenant_id, v_identity.branch_id,
    p_operation_id, 'sale', v_sale_id, 'pos.sale.completed.v1', 1,
    v_event_payload, v_event_payload_hash, 'pending', 0, v_now, v_now,
    p_cashier_user_id, v_now, p_cashier_user_id,
    v_identity.edge_node_id, p_writer_epoch, v_sequence, v_now,
    v_stream_checksum, v_projection_hash, v_projection_checksum
  );

  v_result := v_event_payload || pg_catalog.jsonb_build_object(
    'command_type', 'sale.cash.complete'
  );
  v_result_hash := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(public.edge_canonical_jsonb(v_result), 'UTF8')
    ),
    'hex'
  );

  INSERT INTO public.edge_cash_command (
    id, tenant_id, branch_id, operation_id, edge_identity_id,
    activation_id, edge_node_id, writer_epoch, register_id,
    cashier_user_id, sale_id, sale_status, receipt_number,
    total_amount, currency, command_type, request_hash,
    result_payload, result_hash, created_at, created_by
  ) VALUES (
    pg_catalog.gen_random_uuid(), v_identity.tenant_id, v_identity.branch_id,
    p_operation_id, v_identity.id, p_activation_id,
    v_identity.edge_node_id, p_writer_epoch, v_identity.register_id,
    p_cashier_user_id, v_sale_id, 'completed', v_receipt_number,
    v_total_amount, 'TJS', 'sale.cash.complete', p_request_hash,
    v_result, v_result_hash, v_now, p_cashier_user_id
  );

  RETURN v_result;
END;
$function$
"""


REFERENCE_TABLES = (
    "app_user",
    "batch",
    "branch",
    "edge_cash_command",
    "edge_cash_node_identity",
    "permission",
    "pos_command",
    "pos_payment_attempt",
    "pos_refund_attempt",
    "prescription_log",
    "register",
    "register_receipt_counter",
    "role",
    "role_permission",
    "sale",
    "sale_item",
    "sale_payment",
    "shift",
    "sync_node",
    "sync_outbox",
    "sync_stream",
    "sync_writer_activation",
    "sync_writer_epoch",
    "tenant",
    "tenant_catalog",
    "tenant_membership",
    "tenant_settings",
    "user_assignment",
)


def _secure_helper(signature: str) -> None:
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM aurum_app")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} " "FROM aurum_edge_cash_executor")
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_edge_cash_owner")


def _grant_dispatcher_acl() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO aurum_edge_cash_executor")
    op.execute("GRANT USAGE ON SCHEMA public TO aurum_edge_cash_owner")
    op.execute(
        f"GRANT SELECT ON TABLE public.{', public.'.join(REFERENCE_TABLES)} "
        "TO aurum_edge_cash_owner"
    )
    op.execute(
        "GRANT INSERT ON TABLE public.sale, public.sale_item, public.sale_payment, "
        "public.batch_movement, public.register_receipt_counter, "
        "public.sync_outbox, public.edge_cash_command TO aurum_edge_cash_owner"
    )
    op.execute(
        "GRANT UPDATE ON TABLE public.sale, public.batch, public.shift, "
        "public.register_receipt_counter, public.sync_stream, "
        "public.sync_node, public.sync_writer_activation, "
        "public.sync_writer_epoch "
        "TO aurum_edge_cash_owner"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.current_tenant_id(), "
        "public.current_app_user_id(), "
        "public.is_tenant_support_session(), "
        "public.tenant_actor_is_owner(UUID) "
        "TO aurum_edge_cash_owner"
    )


def upgrade() -> None:
    op.execute(CANONICAL_JSON_SQL)
    op.execute(NORMALIZE_NUMERIC_SQL)
    op.execute(HARDENED_BATCH_QTY_TRIGGER_SQL)
    _secure_helper("public.edge_canonical_jsonb(JSONB)")
    _secure_helper("public.edge_normalize_numeric(NUMERIC)")

    op.execute(ASSERT_BRANCH_WRITER_SQL)
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.assert_current_branch_writer(UUID, UUID, UUID, BOOLEAN) "
        "FROM PUBLIC, aurum_app"
    )

    for table_name in ("edge_cash_node_identity", "edge_cash_command"):
        op.execute(f"""
            CREATE POLICY edge_cash_dispatcher_owner ON public.{table_name}
              TO aurum_edge_cash_owner
              USING (true)
              WITH CHECK (true)
            """)

    op.execute("""
        CREATE POLICY edge_cash_writer_guard_identity
          ON public.edge_cash_node_identity
          FOR SELECT TO aurum_schema_owner
          USING (
            database_role = SESSION_USER
            AND database_role = 'aurum_edge_node_'
              || pg_catalog.replace(edge_node_id::TEXT, '-', '')
            AND database_role_oid = (
              SELECT role.oid
              FROM pg_catalog.pg_roles AS role
              WHERE role.rolname = SESSION_USER
            )
          )
        """)

    op.execute("""
        CREATE POLICY edge_cash_dispatcher_cashier_read ON public.app_user
          FOR SELECT TO aurum_edge_cash_owner
          USING (
            id = NULLIF(
              pg_catalog.current_setting('app.user_id', true),
              ''
            )::UUID
          )
        """)
    op.execute("""
        CREATE POLICY edge_cash_dispatcher_cashier_read
          ON public.tenant_membership
          FOR SELECT TO aurum_edge_cash_owner
          USING (
            tenant_id = public.current_tenant_id()
            AND user_id = NULLIF(
              pg_catalog.current_setting('app.user_id', true),
              ''
            )::UUID
          )
        """)

    op.execute("GRANT USAGE, CREATE ON SCHEMA public TO aurum_edge_cash_owner")
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_edge_cash_owner")
    op.execute(DISPATCHER_SQL)
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {DISPATCH_SIGNATURE} "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_edge_cash_executor"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {DISPATCH_SIGNATURE} " "TO aurum_edge_cash_executor")
    op.execute(
        "COMMENT ON FUNCTION "
        f"{DISPATCH_SIGNATURE} IS "
        "'Atomic cash-only Edge sale dispatcher; identity is bound to session_user'"
    )
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_schema_owner")
    op.execute("REVOKE CREATE ON SCHEMA public FROM aurum_edge_cash_owner")
    _grant_dispatcher_acl()


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM public.edge_cash_command)
            OR EXISTS (SELECT 1 FROM public.edge_cash_node_identity)
          THEN
            RAISE EXCEPTION
              'Refusing to remove the Edge cash dispatcher with enrolled identities';
          END IF;
        END
        $$
        """)
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_edge_cash_owner")
    op.execute(f"DROP FUNCTION {DISPATCH_SIGNATURE}")
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_schema_owner")
    op.execute("DROP POLICY edge_cash_dispatcher_cashier_read " "ON public.tenant_membership")
    op.execute("DROP POLICY edge_cash_dispatcher_cashier_read ON public.app_user")
    op.execute("DROP POLICY edge_cash_writer_guard_identity " "ON public.edge_cash_node_identity")
    for table_name in ("edge_cash_node_identity", "edge_cash_command"):
        op.execute(f"DROP POLICY edge_cash_dispatcher_owner ON public.{table_name}")
    op.execute(LEGACY_ASSERT_BRANCH_WRITER_SQL)
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.assert_current_branch_writer(UUID, UUID, UUID, BOOLEAN) "
        "FROM PUBLIC, aurum_app"
    )
    op.execute("DROP FUNCTION public.edge_normalize_numeric(NUMERIC)")
    op.execute("DROP FUNCTION public.edge_canonical_jsonb(JSONB)")
    op.execute(LEGACY_BATCH_QTY_TRIGGER_SQL)
