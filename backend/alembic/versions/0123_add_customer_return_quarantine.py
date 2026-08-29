"""add immutable customer-return quarantine journals

Revision ID: 0123
Revises: 0122
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0123"
down_revision: str | Sequence[str] | None = "0122"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFERENCE_TABLES = (
    "tenant",
    "branch",
    "sale",
    "sale_item",
    "tenant_catalog",
    "batch",
    "app_user",
)
PERMISSIONS = (
    (
        "customer_returns.view",
        "Просмотр карантина возвратов",
        "Просмотр товаров, возвращённых покупателями и изолированных от продажи.",
        False,
        "normal",
        False,
    ),
    (
        "customer_returns.resolve",
        "Решение по возврату покупателя",
        "Фиксация окончательного решения по товару в карантине.",
        True,
        "sensitive",
        True,
    ),
)


def _grant_missing_reference_privileges() -> None:
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0123_missing_reference_privilege (
          table_name TEXT PRIMARY KEY
        ) ON COMMIT DROP
        """)
    for table_name in REFERENCE_TABLES:
        op.execute(f"""
            DO $$
            BEGIN
              IF NOT pg_catalog.has_table_privilege(
                'aurum_schema_owner', 'public.{table_name}', 'REFERENCES'
              ) THEN
                INSERT INTO pg_temp.aurum_0123_missing_reference_privilege (table_name)
                VALUES ('{table_name}');
                GRANT REFERENCES ON TABLE public.{table_name} TO aurum_schema_owner;
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
                FROM pg_temp.aurum_0123_missing_reference_privilege
                WHERE table_name = '{table_name}'
              ) THEN
                REVOKE REFERENCES ON TABLE public.{table_name} FROM aurum_schema_owner;
              END IF;
            END
            $$
            """)
    op.execute("DROP TABLE pg_temp.aurum_0123_missing_reference_privilege")


def _insert_permissions() -> None:
    # Published role versions are the authorization source of truth, while
    # role_permission remains the editable projection used by role management.
    # The migration role may synchronize that projection only inside this
    # transaction; runtime publication guards stay enabled otherwise.
    op.execute(
        "ALTER TABLE public.role_permission " "DISABLE TRIGGER trg_guard_role_permission_mutation"
    )
    for code, name, description, dangerous, risk, confirmation in PERMISSIONS:
        op.execute(f"""
            INSERT INTO public.permission (
              code, group_code, name, description, min_level_required,
              is_dangerous, is_active, scope_type, target_role_type, risk_level,
              developer_grantable, administrator_grantable, owner_grantable,
              developer_delegable, administrator_delegable, owner_delegable,
              requires_step_up, requires_confirmation
            ) VALUES (
              '{code}', 'customer_returns', '{name}', '{description}', 4,
              {str(dangerous).lower()}, true, 'BRANCH_SET', 'tenant', '{risk}',
              true, true, true, true, true, true, false,
              {str(confirmation).lower()}
            )
            ON CONFLICT (code) DO NOTHING
            """)
        op.execute(f"""
            INSERT INTO public.role_permission (role_id, permission_code)
            SELECT role.id, '{code}'
            FROM public.role AS role
            WHERE role.is_active
              AND (
                (role.is_system AND role.level <= 3)
                OR (role.is_protected AND role.protected_kind = 'tenant_owner')
              )
            ON CONFLICT (role_id, permission_code) DO NOTHING
            """)
        op.execute(f"""
            INSERT INTO public.role_template_permission (template_id, permission_code)
            SELECT template.id, '{code}'
            FROM public.role_template AS template
            WHERE template.slug = 'owner' AND template.is_active
            ON CONFLICT (template_id, permission_code) DO NOTHING
            """)
        op.execute(f"""
            INSERT INTO public.access_role_version_permission (
              role_version_id, permission_code, created_at
            )
            SELECT version.id, '{code}', pg_catalog.statement_timestamp()
            FROM public.access_role_version AS version
            JOIN public.role AS role ON role.id = version.role_id
            WHERE version.status = 'published'
              AND role.is_active
              AND (
                (role.is_system AND role.level <= 3)
                OR (role.is_protected AND role.protected_kind = 'tenant_owner')
              )
            ON CONFLICT (role_version_id, permission_code) DO NOTHING
            """)
    op.execute(
        "ALTER TABLE public.role_permission " "ENABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("SELECT public.bump_all_authorization_policy_revisions()")


def upgrade() -> None:
    _grant_missing_reference_privileges()
    op.execute("""
        ALTER TABLE public.branch
          ADD CONSTRAINT uq_branch_tenant_id_id UNIQUE (tenant_id, id)
        """)
    op.execute("""
        ALTER TABLE public.sale_item
          ADD CONSTRAINT uq_sale_item_tenant_id_id UNIQUE (tenant_id, id)
        """)
    op.execute("""
        CREATE TABLE public.customer_return_quarantine_item (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          branch_id UUID NOT NULL,
          return_sale_id UUID NOT NULL,
          return_sale_item_id UUID NOT NULL,
          parent_sale_id UUID NOT NULL,
          parent_sale_item_id UUID NOT NULL,
          catalog_id UUID NOT NULL,
          batch_id UUID NOT NULL,
          qty NUMERIC(14,3) NOT NULL,
          refund_reason TEXT,
          refund_comment TEXT,
          received_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          received_by UUID NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_customer_return_quarantine_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT uq_customer_return_quarantine_return_item
            UNIQUE (tenant_id, return_sale_item_id),
          CONSTRAINT ck_customer_return_quarantine_qty CHECK (qty > 0),
          CONSTRAINT ck_customer_return_quarantine_reason
            CHECK (refund_reason IS NULL OR char_length(refund_reason) BETWEEN 1 AND 500),
          CONSTRAINT ck_customer_return_quarantine_comment
            CHECK (refund_comment IS NULL OR char_length(refund_comment) BETWEEN 1 AND 2000),
          CONSTRAINT fk_customer_return_quarantine_tenant
            FOREIGN KEY (tenant_id) REFERENCES public.tenant(id) ON DELETE RESTRICT,
          CONSTRAINT fk_customer_return_quarantine_branch
            FOREIGN KEY (tenant_id, branch_id)
            REFERENCES public.branch(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_customer_return_quarantine_return_sale
            FOREIGN KEY (tenant_id, return_sale_id)
            REFERENCES public.sale(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_customer_return_quarantine_return_item
            FOREIGN KEY (tenant_id, return_sale_item_id)
            REFERENCES public.sale_item(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_customer_return_quarantine_parent_sale
            FOREIGN KEY (tenant_id, parent_sale_id)
            REFERENCES public.sale(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_customer_return_quarantine_parent_item
            FOREIGN KEY (tenant_id, parent_sale_item_id)
            REFERENCES public.sale_item(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_customer_return_quarantine_catalog
            FOREIGN KEY (tenant_id, catalog_id)
            REFERENCES public.tenant_catalog(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_customer_return_quarantine_batch
            FOREIGN KEY (tenant_id, batch_id)
            REFERENCES public.batch(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_customer_return_quarantine_actor
            FOREIGN KEY (received_by) REFERENCES public.app_user(id) ON DELETE RESTRICT
        )
        """)
    op.execute("""
        CREATE INDEX ix_customer_return_quarantine_pending
        ON public.customer_return_quarantine_item (tenant_id, branch_id, received_at DESC)
        """)
    op.execute("""
        CREATE INDEX ix_customer_return_quarantine_catalog
        ON public.customer_return_quarantine_item (tenant_id, catalog_id)
        """)
    op.execute("""
        CREATE TABLE public.customer_return_disposition (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          branch_id UUID NOT NULL,
          quarantine_item_id UUID NOT NULL,
          operation_id UUID NOT NULL,
          operation_hash CHAR(64) NOT NULL,
          decision TEXT NOT NULL,
          reason TEXT NOT NULL,
          comment TEXT,
          resolved_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          resolved_by UUID NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_customer_return_disposition_operation
            UNIQUE (tenant_id, operation_id),
          CONSTRAINT uq_customer_return_disposition_item
            UNIQUE (tenant_id, quarantine_item_id),
          CONSTRAINT ck_customer_return_disposition_hash
            CHECK (operation_hash ~ '^[0-9a-f]{64}$'),
          CONSTRAINT ck_customer_return_disposition_decision CHECK (
            decision IN (
              'disposed', 'supplier_claim', 'regulatory_transfer'
            )
          ),
          CONSTRAINT ck_customer_return_disposition_reason
            CHECK (reason IN (
              'damaged', 'quality_issue', 'wrong_item', 'expired', 'other'
            )),
          CONSTRAINT ck_customer_return_disposition_comment
            CHECK (comment IS NULL OR char_length(comment) BETWEEN 1 AND 2000),
          CONSTRAINT fk_customer_return_disposition_tenant
            FOREIGN KEY (tenant_id) REFERENCES public.tenant(id) ON DELETE RESTRICT,
          CONSTRAINT fk_customer_return_disposition_branch
            FOREIGN KEY (tenant_id, branch_id)
            REFERENCES public.branch(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_customer_return_disposition_item
            FOREIGN KEY (tenant_id, quarantine_item_id)
            REFERENCES public.customer_return_quarantine_item(tenant_id, id)
            ON DELETE RESTRICT,
          CONSTRAINT fk_customer_return_disposition_actor
            FOREIGN KEY (resolved_by) REFERENCES public.app_user(id) ON DELETE RESTRICT
        )
        """)

    for table_name in (
        "customer_return_quarantine_item",
        "customer_return_disposition",
    ):
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table_name}_schema_owner
            ON public.{table_name} TO aurum_schema_owner
            USING (true) WITH CHECK (true)
            """)
        op.execute(f"""
            CREATE POLICY {table_name}_tenant_access
            ON public.{table_name} TO aurum_app, aurum_support
            USING (tenant_id = public.current_tenant_id())
            WITH CHECK (tenant_id = public.current_tenant_id())
            """)
        op.execute(f"""
            REVOKE ALL PRIVILEGES ON TABLE public.{table_name}
            FROM PUBLIC, aurum_app, aurum_support
            """)
        op.execute(f"""
            GRANT SELECT, INSERT ON TABLE public.{table_name}
            TO aurum_app, aurum_support
            """)

    op.execute("""
        CREATE FUNCTION public.trg_reject_customer_return_journal_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
          RAISE EXCEPTION 'Customer-return journals are immutable'
            USING ERRCODE = '42501';
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        """)
    op.execute("""
        REVOKE ALL PRIVILEGES ON FUNCTION
          public.trg_reject_customer_return_journal_mutation()
        FROM PUBLIC, aurum_app, aurum_support
        """)
    op.execute("""
        CREATE TRIGGER trg_immutable_customer_return_quarantine
        BEFORE UPDATE OR DELETE ON public.customer_return_quarantine_item
        FOR EACH ROW EXECUTE FUNCTION
          public.trg_reject_customer_return_journal_mutation()
        """)
    op.execute("""
        CREATE TRIGGER trg_immutable_customer_return_disposition
        BEFORE UPDATE OR DELETE ON public.customer_return_disposition
        FOR EACH ROW EXECUTE FUNCTION
          public.trg_reject_customer_return_journal_mutation()
        """)
    op.execute("""
        CREATE FUNCTION public.trg_audit_customer_return_quarantine()
        RETURNS TRIGGER AS $$
        BEGIN
          INSERT INTO public.audit_log (
            tenant_id, user_id, action, table_name, record_id, metadata, created_at
          ) VALUES (
            NEW.tenant_id, NEW.received_by, 'INSERT',
            'customer_return_quarantine_item', NEW.id,
            jsonb_build_object(
              'branch_id', NEW.branch_id,
              'return_sale_id', NEW.return_sale_id,
              'return_sale_item_id', NEW.return_sale_item_id,
              'parent_sale_id', NEW.parent_sale_id,
              'parent_sale_item_id', NEW.parent_sale_item_id,
              'catalog_id', NEW.catalog_id,
              'batch_id', NEW.batch_id,
              'qty', NEW.qty
            ), NEW.created_at
          );
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        """)
    op.execute("""
        CREATE FUNCTION public.trg_audit_customer_return_disposition()
        RETURNS TRIGGER AS $$
        BEGIN
          INSERT INTO public.audit_log (
            tenant_id, user_id, action, table_name, record_id, metadata, created_at
          ) VALUES (
            NEW.tenant_id, NEW.resolved_by, 'INSERT',
            'customer_return_disposition', NEW.id,
            jsonb_build_object(
              'branch_id', NEW.branch_id,
              'quarantine_item_id', NEW.quarantine_item_id,
              'operation_id', NEW.operation_id,
              'decision', NEW.decision
            ), NEW.created_at
          );
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        """)
    op.execute("""
        REVOKE ALL PRIVILEGES ON FUNCTION
          public.trg_audit_customer_return_quarantine(),
          public.trg_audit_customer_return_disposition()
        FROM PUBLIC, aurum_app, aurum_support
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_customer_return_quarantine
        AFTER INSERT ON public.customer_return_quarantine_item
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_customer_return_quarantine()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_customer_return_disposition
        AFTER INSERT ON public.customer_return_disposition
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_customer_return_disposition()
        """)

    # Historical returns cannot be subtracted safely because some may have
    # already been sold again. Keep them pending and block every affected batch
    # until a human reconciles its physical stock.
    op.execute("""
        INSERT INTO public.customer_return_quarantine_item (
          tenant_id, branch_id, return_sale_id, return_sale_item_id,
          parent_sale_id, parent_sale_item_id, catalog_id, batch_id, qty,
          refund_reason, refund_comment, received_at, received_by, created_at
        )
        SELECT
          return_sale.tenant_id, return_sale.branch_id, return_sale.id, return_item.id,
          return_sale.parent_sale_id, return_item.parent_sale_item_id,
          return_item.catalog_id, return_item.batch_id, return_item.qty,
          NULLIF(payment.metadata ->> 'reason', ''),
          NULLIF(payment.metadata ->> 'comment', ''),
          COALESCE(return_sale.completed_at, return_sale.created_at),
          return_sale.cashier_user_id,
          COALESCE(return_sale.completed_at, return_sale.created_at)
        FROM public.sale AS return_sale
        JOIN public.sale_item AS return_item ON return_item.sale_id = return_sale.id
        LEFT JOIN LATERAL (
          SELECT sale_payment.metadata
          FROM public.sale_payment
          WHERE sale_payment.sale_id = return_sale.id
          ORDER BY sale_payment.created_at, sale_payment.id
          LIMIT 1
        ) AS payment ON true
        WHERE return_sale.sale_type = 'return'
          AND return_sale.status = 'completed'
          AND NOT return_sale.is_test
        ON CONFLICT (tenant_id, return_sale_item_id) DO NOTHING
        """)
    op.execute("ALTER TABLE public.batch DISABLE TRIGGER trg_batch_writer_guard")
    op.execute("""
        UPDATE public.batch AS batch
        SET is_blocked = true,
            block_reason = COALESCE(
              NULLIF(batch.block_reason, ''),
              'Требуется сверка исторических возвратов покупателей'
            ),
            updated_at = pg_catalog.statement_timestamp()
        WHERE EXISTS (
          SELECT 1
          FROM public.customer_return_quarantine_item AS item
          WHERE item.tenant_id = batch.tenant_id
            AND item.batch_id = batch.id
        )
        """)
    op.execute("ALTER TABLE public.batch ENABLE TRIGGER trg_batch_writer_guard")

    op.execute("""
        CREATE FUNCTION public.trg_validate_customer_return_quarantine_insert()
        RETURNS TRIGGER AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
            FROM public.sale AS return_sale
            JOIN public.sale_item AS return_item
              ON return_item.sale_id = return_sale.id
             AND return_item.id = NEW.return_sale_item_id
             AND return_item.tenant_id = NEW.tenant_id
            JOIN public.sale AS parent_sale
              ON parent_sale.id = return_sale.parent_sale_id
             AND parent_sale.id = NEW.parent_sale_id
             AND parent_sale.tenant_id = NEW.tenant_id
            JOIN public.sale_item AS parent_item
              ON parent_item.id = return_item.parent_sale_item_id
             AND parent_item.id = NEW.parent_sale_item_id
             AND parent_item.sale_id = parent_sale.id
             AND parent_item.tenant_id = NEW.tenant_id
            JOIN public.batch AS batch
              ON batch.id = return_item.batch_id
             AND batch.tenant_id = NEW.tenant_id
             AND batch.branch_id = NEW.branch_id
             AND batch.catalog_id = return_item.catalog_id
            WHERE return_sale.id = NEW.return_sale_id
              AND return_sale.tenant_id = NEW.tenant_id
              AND return_sale.branch_id = NEW.branch_id
              AND return_sale.sale_type = 'return'
              AND return_sale.status = 'draft'
              AND NOT return_sale.is_test
              AND return_item.catalog_id = NEW.catalog_id
              AND return_item.batch_id = NEW.batch_id
              AND return_item.qty = NEW.qty
              AND return_sale.cashier_user_id = NEW.received_by
          ) THEN
            RAISE EXCEPTION 'Invalid customer-return quarantine identity'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        """)
    op.execute("""
        CREATE FUNCTION public.trg_validate_customer_return_disposition_insert()
        RETURNS TRIGGER AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
            FROM public.customer_return_quarantine_item AS item
            JOIN public.sale AS return_sale
              ON return_sale.id = item.return_sale_id
             AND return_sale.tenant_id = item.tenant_id
            WHERE item.id = NEW.quarantine_item_id
              AND item.tenant_id = NEW.tenant_id
              AND item.branch_id = NEW.branch_id
              AND return_sale.status = 'completed'
              AND return_sale.sale_type = 'return'
              AND NOT return_sale.is_test
          ) THEN
            RAISE EXCEPTION 'Invalid customer-return disposition identity'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        """)
    op.execute("""
        CREATE FUNCTION public.trg_require_return_quarantine_before_completion()
        RETURNS TRIGGER AS $$
        BEGIN
          IF NEW.status = 'completed'
             AND OLD.status IS DISTINCT FROM NEW.status
             AND NEW.sale_type = 'return'
             AND NOT NEW.is_test
             AND EXISTS (
               SELECT 1
               FROM public.sale_item AS return_item
               LEFT JOIN public.customer_return_quarantine_item AS quarantine
                 ON quarantine.tenant_id = return_item.tenant_id
                AND quarantine.return_sale_id = NEW.id
                AND quarantine.return_sale_item_id = return_item.id
                AND quarantine.parent_sale_id = NEW.parent_sale_id
                AND quarantine.parent_sale_item_id = return_item.parent_sale_item_id
                AND quarantine.catalog_id = return_item.catalog_id
                AND quarantine.batch_id = return_item.batch_id
                AND quarantine.qty = return_item.qty
                AND quarantine.branch_id = NEW.branch_id
               WHERE return_item.sale_id = NEW.id
                 AND quarantine.id IS NULL
             ) THEN
            RAISE EXCEPTION 'Every real return item requires quarantine before completion'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        """)
    op.execute("""
        REVOKE ALL PRIVILEGES ON FUNCTION
          public.trg_validate_customer_return_quarantine_insert(),
          public.trg_validate_customer_return_disposition_insert(),
          public.trg_require_return_quarantine_before_completion()
        FROM PUBLIC, aurum_app, aurum_support
        """)
    op.execute("""
        CREATE TRIGGER trg_validate_customer_return_quarantine_insert
        BEFORE INSERT ON public.customer_return_quarantine_item
        FOR EACH ROW EXECUTE FUNCTION
          public.trg_validate_customer_return_quarantine_insert()
        """)
    op.execute("""
        CREATE TRIGGER trg_validate_customer_return_disposition_insert
        BEFORE INSERT ON public.customer_return_disposition
        FOR EACH ROW EXECUTE FUNCTION
          public.trg_validate_customer_return_disposition_insert()
        """)
    op.execute("""
        CREATE TRIGGER trg_require_return_quarantine_before_completion
        BEFORE UPDATE OF status ON public.sale
        FOR EACH ROW EXECUTE FUNCTION
          public.trg_require_return_quarantine_before_completion()
        """)

    _insert_permissions()
    _restore_reference_privileges()


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_require_return_quarantine_before_completion ON public.sale")
    op.execute("DROP FUNCTION public.trg_require_return_quarantine_before_completion()")
    op.execute(
        "DROP TRIGGER trg_validate_customer_return_disposition_insert "
        "ON public.customer_return_disposition"
    )
    op.execute("DROP FUNCTION public.trg_validate_customer_return_disposition_insert()")
    op.execute(
        "DROP TRIGGER trg_validate_customer_return_quarantine_insert "
        "ON public.customer_return_quarantine_item"
    )
    op.execute("DROP FUNCTION public.trg_validate_customer_return_quarantine_insert()")
    op.execute(
        "DROP TRIGGER trg_audit_customer_return_disposition "
        "ON public.customer_return_disposition"
    )
    op.execute("DROP FUNCTION public.trg_audit_customer_return_disposition()")
    op.execute(
        "DROP TRIGGER trg_audit_customer_return_quarantine "
        "ON public.customer_return_quarantine_item"
    )
    op.execute("DROP FUNCTION public.trg_audit_customer_return_quarantine()")
    op.execute(
        "DROP TRIGGER trg_immutable_customer_return_disposition "
        "ON public.customer_return_disposition"
    )
    op.execute(
        "DROP TRIGGER trg_immutable_customer_return_quarantine "
        "ON public.customer_return_quarantine_item"
    )
    op.execute("DROP FUNCTION public.trg_reject_customer_return_journal_mutation()")
    op.execute("DROP TABLE public.customer_return_disposition")
    op.execute("DROP TABLE public.customer_return_quarantine_item")
    op.execute("ALTER TABLE public.sale_item DROP CONSTRAINT uq_sale_item_tenant_id_id")
    op.execute("ALTER TABLE public.branch DROP CONSTRAINT uq_branch_tenant_id_id")
    codes = ", ".join(f"'{permission[0]}'" for permission in PERMISSIONS)
    op.execute(
        f"DELETE FROM public.access_role_version_permission WHERE permission_code IN ({codes})"
    )
    op.execute(f"DELETE FROM public.role_template_permission WHERE permission_code IN ({codes})")
    op.execute(
        "ALTER TABLE public.role_permission " "DISABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute(f"DELETE FROM public.role_permission WHERE permission_code IN ({codes})")
    op.execute(
        "ALTER TABLE public.role_permission " "ENABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute(f"DELETE FROM public.permission WHERE code IN ({codes})")
    op.execute("SELECT public.bump_all_authorization_policy_revisions()")
