"""guard password-required assignments against missing passwords

Revision ID: 0112
Revises: 0111
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0112"
down_revision: str | Sequence[str] | None = "0111"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ASSIGNMENT_GUARD = "public.trg_enforce_assignment_password_requirement()"
ACCOUNT_GUARD = "public.trg_enforce_account_password_requirement()"


def _secure_trigger_function(signature: str) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(f"ALTER FUNCTION {signature} SECURITY DEFINER")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer, "
        "aurum_billing_worker, aurum_edge_cash_executor, aurum_edge_cash_owner"
    )


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.app_user
        ADD COLUMN password_configured BOOLEAN
        GENERATED ALWAYS AS (password_hash IS NOT NULL) STORED
        """)
    op.execute("GRANT SELECT (password_configured) ON TABLE public.app_user TO aurum_app")

    # Earlier versions allowed this contradictory state. Normalizing the flag
    # restores code-only login and never broadens the user's permissions.
    op.execute(
        "ALTER TABLE public.user_assignment " "DISABLE TRIGGER trg_guard_user_assignment_scope"
    )
    op.execute("""
        UPDATE public.user_assignment AS assignment
        SET password_required = FALSE
        FROM public.app_user AS account
        WHERE account.id = assignment.user_id
          AND NOT account.password_configured
          AND assignment.password_required
        """)
    op.execute(
        "ALTER TABLE public.user_assignment " "ENABLE TRIGGER trg_guard_user_assignment_scope"
    )

    op.execute("""
        CREATE FUNCTION public.trg_enforce_assignment_password_requirement()
        RETURNS TRIGGER AS $$
        BEGIN
          IF NOT NEW.is_active OR NOT NEW.password_required THEN
            RETURN NEW;
          END IF;

          PERFORM account.id
          FROM public.app_user AS account
          WHERE account.id = NEW.user_id
            AND account.password_configured
          FOR UPDATE;

          IF NOT FOUND THEN
            RAISE EXCEPTION
              'Password must be configured before it can be required at login'
              USING
                ERRCODE = 'P2001',
                CONSTRAINT = 'ck_user_assignment_password_configured';
          END IF;

          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        """)
    _secure_trigger_function(ASSIGNMENT_GUARD)
    op.execute("""
        CREATE TRIGGER trg_enforce_assignment_password_requirement
        AFTER INSERT OR UPDATE OF user_id, is_active, password_required
        ON public.user_assignment
        FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_assignment_password_requirement()
        """)

    op.execute("""
        CREATE FUNCTION public.trg_enforce_account_password_requirement()
        RETURNS TRIGGER AS $$
        BEGIN
          IF NEW.password_configured THEN
            RETURN NEW;
          END IF;

          IF EXISTS (
            SELECT 1
            FROM public.user_assignment AS assignment
            WHERE assignment.user_id = NEW.id
              AND assignment.is_active
              AND assignment.password_required
          ) THEN
            RAISE EXCEPTION
              'Password cannot be removed while an active assignment requires it'
              USING
                ERRCODE = 'P2001',
                CONSTRAINT = 'ck_user_assignment_password_configured';
          END IF;

          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        """)
    _secure_trigger_function(ACCOUNT_GUARD)
    op.execute("""
        CREATE TRIGGER trg_enforce_account_password_requirement
        AFTER UPDATE OF password_hash ON public.app_user
        FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_account_password_requirement()
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_enforce_account_password_requirement " "ON public.app_user"
    )
    op.execute(f"DROP FUNCTION {ACCOUNT_GUARD}")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_enforce_assignment_password_requirement "
        "ON public.user_assignment"
    )
    op.execute(f"DROP FUNCTION {ASSIGNMENT_GUARD}")
    op.execute("REVOKE SELECT (password_configured) ON TABLE public.app_user FROM aurum_app")
    op.execute("ALTER TABLE public.app_user DROP COLUMN password_configured")
