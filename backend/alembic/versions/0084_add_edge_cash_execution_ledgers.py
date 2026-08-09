"""add immutable Edge database identity and cash command ledgers

Revision ID: 0084
Revises: 0083
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0084"
down_revision: str | Sequence[str] | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.sync_node
          ADD CONSTRAINT uq_sync_node_edge_cash_scope
          UNIQUE (id, tenant_id, branch_id, register_id)
        """)
    op.execute("""
        ALTER TABLE public.sync_writer_activation
          ADD CONSTRAINT uq_sync_writer_activation_edge_cash_scope
          UNIQUE (
            activation_id, tenant_id, branch_id, writer_epoch,
            writer_node_id, allowed_register_id
          )
        """)
    op.execute("""
        ALTER TABLE public.sync_writer_epoch
          ADD CONSTRAINT uq_sync_writer_epoch_edge_cash_scope
          UNIQUE (
            activation_id, tenant_id, branch_id, writer_epoch,
            writer_node_id, allowed_register_id
          )
        """)
    op.execute("""
        ALTER TABLE public.sale
          ADD CONSTRAINT uq_sale_edge_cash_scope
          UNIQUE (
            id, tenant_id, branch_id, register_id,
            cashier_user_id, operation_id, status,
            receipt_number, total_amount, currency
          )
        """)

    op.execute("""
        CREATE TABLE public.edge_cash_node_identity (
          id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id      UUID NOT NULL REFERENCES public.tenant(id) ON DELETE CASCADE,
          branch_id      UUID NOT NULL REFERENCES public.branch(id) ON DELETE RESTRICT,
          edge_node_id   UUID NOT NULL,
          register_id    UUID NOT NULL,
          database_role  TEXT NOT NULL,
          database_role_oid OID NOT NULL,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by     UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_edge_cash_node_identity_role UNIQUE (database_role),
          CONSTRAINT uq_edge_cash_node_identity_role_oid UNIQUE (database_role_oid),
          CONSTRAINT uq_edge_cash_node_identity_node UNIQUE (edge_node_id),
          CONSTRAINT uq_edge_cash_node_identity_scope
            UNIQUE (id, tenant_id, branch_id, edge_node_id, register_id),
          CONSTRAINT fk_edge_cash_node_identity_node_scope
            FOREIGN KEY (edge_node_id, tenant_id, branch_id, register_id)
            REFERENCES public.sync_node(id, tenant_id, branch_id, register_id)
            ON DELETE RESTRICT,
          CONSTRAINT ck_edge_cash_node_identity_role CHECK (
            database_role ~ '^aurum_edge_node_[0-9a-f]{32}$'
            AND octet_length(database_role) <= 63
          ),
          CONSTRAINT ck_edge_cash_node_identity_role_oid CHECK (
            database_role_oid <> 0
          )
        )
        """)

    op.execute("""
        CREATE TABLE public.edge_cash_command (
          id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id         UUID NOT NULL REFERENCES public.tenant(id) ON DELETE CASCADE,
          branch_id         UUID NOT NULL REFERENCES public.branch(id) ON DELETE RESTRICT,
          operation_id      UUID NOT NULL,
          edge_identity_id  UUID NOT NULL,
          activation_id     UUID NOT NULL,
          edge_node_id      UUID NOT NULL,
          writer_epoch      BIGINT NOT NULL,
          register_id       UUID NOT NULL,
          cashier_user_id   UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          sale_id           UUID NOT NULL,
          sale_status       TEXT NOT NULL DEFAULT 'completed',
          receipt_number    TEXT NOT NULL,
          total_amount      NUMERIC(14,2) NOT NULL,
          currency          TEXT NOT NULL DEFAULT 'TJS',
          command_type      TEXT NOT NULL DEFAULT 'sale.cash.complete',
          request_hash      TEXT NOT NULL,
          result_payload    JSONB NOT NULL,
          result_hash       TEXT NOT NULL,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by        UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_edge_cash_command_tenant_operation
            UNIQUE (tenant_id, operation_id),
          CONSTRAINT uq_edge_cash_command_tenant_sale
            UNIQUE (tenant_id, sale_id),
          CONSTRAINT uq_edge_cash_command_tenant_id
            UNIQUE (tenant_id, id),
          CONSTRAINT fk_edge_cash_command_identity_scope
            FOREIGN KEY (
              edge_identity_id, tenant_id, branch_id, edge_node_id, register_id
            )
            REFERENCES public.edge_cash_node_identity(
              id, tenant_id, branch_id, edge_node_id, register_id
            ) ON DELETE RESTRICT,
          CONSTRAINT fk_edge_cash_command_activation_scope
            FOREIGN KEY (
              activation_id, tenant_id, branch_id, writer_epoch,
              edge_node_id, register_id
            ) REFERENCES public.sync_writer_activation(
              activation_id, tenant_id, branch_id, writer_epoch,
              writer_node_id, allowed_register_id
            ) ON DELETE RESTRICT,
          CONSTRAINT fk_edge_cash_command_epoch_scope
            FOREIGN KEY (
              activation_id, tenant_id, branch_id, writer_epoch,
              edge_node_id, register_id
            ) REFERENCES public.sync_writer_epoch(
              activation_id, tenant_id, branch_id, writer_epoch,
              writer_node_id, allowed_register_id
            ) ON DELETE RESTRICT,
          CONSTRAINT fk_edge_cash_command_register_scope
            FOREIGN KEY (register_id, tenant_id, branch_id)
            REFERENCES public.register(id, tenant_id, branch_id)
            ON DELETE RESTRICT,
          CONSTRAINT fk_edge_cash_command_sale_scope
            FOREIGN KEY (
              sale_id, tenant_id, branch_id, register_id,
              cashier_user_id, operation_id, sale_status,
              receipt_number, total_amount, currency
            ) REFERENCES public.sale(
              id, tenant_id, branch_id, register_id,
              cashier_user_id, operation_id, status,
              receipt_number, total_amount, currency
            )
            ON DELETE RESTRICT,
          CONSTRAINT ck_edge_cash_command_operation_uuid4 CHECK (
            (get_byte(uuid_send(operation_id), 6) >> 4) = 4
            AND (get_byte(uuid_send(operation_id), 8) & 192) = 128
          ),
          CONSTRAINT ck_edge_cash_command_writer_epoch CHECK (writer_epoch > 0),
          CONSTRAINT ck_edge_cash_command_type CHECK (
            command_type = 'sale.cash.complete'
          ),
          CONSTRAINT ck_edge_cash_command_sale_status CHECK (
            sale_status = 'completed'
          ),
          CONSTRAINT ck_edge_cash_command_receipt_number CHECK (
            btrim(receipt_number) <> ''
          ),
          CONSTRAINT ck_edge_cash_command_total_amount CHECK (
            total_amount >= 0
          ),
          CONSTRAINT ck_edge_cash_command_currency CHECK (
            currency = 'TJS'
          ),
          CONSTRAINT ck_edge_cash_command_request_hash CHECK (
            request_hash ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT ck_edge_cash_command_result_hash CHECK (
            result_hash ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT ck_edge_cash_command_result_payload CHECK (
            jsonb_typeof(result_payload) = 'object'
            AND octet_length(result_payload::text) BETWEEN 2 AND 131072
            AND result_payload ->> 'command_type' = command_type
            AND result_payload ->> 'operation_id' = operation_id::text
            AND result_payload ->> 'sale_id' = sale_id::text
            AND result_payload ->> 'receipt_number' = receipt_number
            AND result_payload ->> 'total_amount' = to_char(
              total_amount, 'FM9999999999990.00'
            )
            AND result_payload ->> 'currency' = currency
          )
        )
        """)
    op.execute("""
        CREATE INDEX ix_edge_cash_command_node_created
          ON public.edge_cash_command (
            tenant_id, edge_node_id, writer_epoch, created_at
          )
        """)
    for table_name in ("edge_cash_node_identity", "edge_cash_command"):
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY")

    op.execute("""
        CREATE FUNCTION public.trg_validate_edge_cash_node_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
        DECLARE
          v_executor_oid OID;
        BEGIN
          SELECT roles.oid
          INTO v_executor_oid
          FROM pg_catalog.pg_roles AS roles
          WHERE roles.rolname = 'aurum_edge_cash_executor';

          IF v_executor_oid IS NULL OR NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles AS roles
            WHERE roles.rolname = NEW.database_role
              AND roles.oid = NEW.database_role_oid
              AND roles.rolcanlogin
              AND roles.rolinherit
              AND NOT roles.rolsuper
              AND NOT roles.rolcreatedb
              AND NOT roles.rolcreaterole
              AND NOT roles.rolreplication
              AND NOT roles.rolbypassrls
          ) THEN
            RAISE EXCEPTION 'Edge database role identity does not match'
              USING ERRCODE = '42501';
          END IF;

          IF (
            SELECT pg_catalog.count(*)
            FROM pg_catalog.pg_auth_members AS memberships
            WHERE memberships.member = NEW.database_role_oid
          ) <> 1 OR NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_auth_members AS memberships
            WHERE memberships.member = NEW.database_role_oid
              AND memberships.roleid = v_executor_oid
              AND NOT memberships.admin_option
              AND memberships.inherit_option
              AND NOT memberships.set_option
          ) THEN
            RAISE EXCEPTION 'Edge database role membership is unsafe'
              USING ERRCODE = '42501';
          END IF;

          RETURN NEW;
        END;
        $function$
        """)
    op.execute("""
        REVOKE ALL ON FUNCTION public.trg_validate_edge_cash_node_identity()
          FROM PUBLIC, aurum_app, aurum_support,
               aurum_edge_cash_executor, aurum_edge_cash_owner
        """)

    op.execute("""
        CREATE FUNCTION public.trg_guard_edge_cash_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
          RAISE EXCEPTION 'Edge cash security ledgers are append-only'
            USING ERRCODE = '55000';
        END;
        $function$
        """)
    op.execute("""
        REVOKE ALL ON FUNCTION public.trg_guard_edge_cash_append_only()
          FROM PUBLIC, aurum_app, aurum_support,
               aurum_edge_cash_executor, aurum_edge_cash_owner
        """)

    op.execute("""
        CREATE FUNCTION public.trg_audit_edge_cash_command_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
        BEGIN
          INSERT INTO public.audit_log (
            tenant_id, user_id, action, table_name, record_id,
            old_values, new_values, changed_fields, created_at
          ) VALUES (
            NEW.tenant_id,
            NEW.cashier_user_id,
            'INSERT',
            'edge_cash_command',
            NEW.id,
            NULL,
            pg_catalog.jsonb_build_object(
              'operation_id', NEW.operation_id,
              'activation_id', NEW.activation_id,
              'edge_node_id', NEW.edge_node_id,
              'writer_epoch', NEW.writer_epoch,
              'register_id', NEW.register_id,
              'cashier_user_id', NEW.cashier_user_id,
              'sale_id', NEW.sale_id,
              'sale_status', NEW.sale_status,
              'command_type', NEW.command_type,
              'request_hash', NEW.request_hash,
              'result_hash', NEW.result_hash
            ),
            NULL,
            pg_catalog.now()
          );
          RETURN NEW;
        END;
        $function$
        """)
    op.execute("""
        REVOKE ALL ON FUNCTION public.trg_audit_edge_cash_command_insert()
          FROM PUBLIC, aurum_app, aurum_support,
               aurum_edge_cash_executor, aurum_edge_cash_owner
        """)

    op.execute("""
        CREATE TRIGGER trg_edge_cash_node_identity_validate
          BEFORE INSERT ON public.edge_cash_node_identity
          FOR EACH ROW EXECUTE FUNCTION public.trg_validate_edge_cash_node_identity()
        """)
    op.execute("""
        CREATE TRIGGER trg_edge_cash_node_identity_immutable
          BEFORE UPDATE OR DELETE ON public.edge_cash_node_identity
          FOR EACH ROW EXECUTE FUNCTION public.trg_guard_edge_cash_append_only()
        """)
    op.execute("""
        CREATE TRIGGER trg_edge_cash_node_identity_created
          BEFORE INSERT ON public.edge_cash_node_identity
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_edge_cash_node_identity
          AFTER INSERT OR UPDATE OR DELETE ON public.edge_cash_node_identity
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)
    op.execute("""
        CREATE TRIGGER trg_edge_cash_command_immutable
          BEFORE UPDATE OR DELETE ON public.edge_cash_command
          FOR EACH ROW EXECUTE FUNCTION public.trg_guard_edge_cash_append_only()
        """)
    op.execute("""
        CREATE TRIGGER trg_edge_cash_command_created
          BEFORE INSERT ON public.edge_cash_command
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_edge_cash_command
          AFTER INSERT ON public.edge_cash_command
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_edge_cash_command_insert()
        """)

    for table_name in ("edge_cash_node_identity", "edge_cash_command"):
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE public.{table_name} "
            "FROM PUBLIC, aurum_app, aurum_support, "
            "aurum_edge_cash_executor, aurum_edge_cash_owner"
        )


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM public.edge_cash_command)
            OR EXISTS (SELECT 1 FROM public.edge_cash_node_identity)
          THEN
            RAISE EXCEPTION
              'Refusing to remove non-empty Edge cash security ledgers';
          END IF;
        END
        $$
        """)
    op.execute("DROP TABLE public.edge_cash_command")
    op.execute("DROP TABLE public.edge_cash_node_identity")
    op.execute("DROP FUNCTION public.trg_audit_edge_cash_command_insert()")
    op.execute("DROP FUNCTION public.trg_validate_edge_cash_node_identity()")
    op.execute("DROP FUNCTION public.trg_guard_edge_cash_append_only()")
    op.execute("""
        ALTER TABLE public.sync_writer_epoch
          DROP CONSTRAINT uq_sync_writer_epoch_edge_cash_scope
        """)
    op.execute("""
        ALTER TABLE public.sync_writer_activation
          DROP CONSTRAINT uq_sync_writer_activation_edge_cash_scope
        """)
    op.execute("""
        ALTER TABLE public.sale
          DROP CONSTRAINT uq_sale_edge_cash_scope
        """)
    op.execute("""
        ALTER TABLE public.sync_node
          DROP CONSTRAINT uq_sync_node_edge_cash_scope
        """)
