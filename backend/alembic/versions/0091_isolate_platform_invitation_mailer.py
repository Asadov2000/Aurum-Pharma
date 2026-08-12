"""isolate platform invitation delivery behind a dedicated mailer role

Revision ID: 0091
Revises: 0090
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0091"
down_revision: str | None = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENQUEUE = (
    "public.enqueue_platform_invitation_email("
    "UUID, UUID, UUID, INTEGER, TEXT, TEXT, SMALLINT, TEXT)"
)
CLAIM = "public.claim_platform_invitation_email(JSONB, INTEGER)"
COMPLETE = "public.complete_platform_invitation_email(UUID, UUID, TEXT, TEXT)"


def _make_definer(signature: str, grantee: str) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(f"ALTER FUNCTION {signature} SECURITY DEFINER")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grantee}")


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO aurum_mailer")
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.platform_email_outbox "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer"
    )
    _make_definer(ENQUEUE, "aurum_support")
    _make_definer(CLAIM, "aurum_mailer")
    _make_definer(COMPLETE, "aurum_mailer")


def downgrade() -> None:
    for signature in (ENQUEUE, CLAIM, COMPLETE):
        op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
        op.execute(f"ALTER FUNCTION {signature} SECURITY INVOKER")
        op.execute(
            f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
            "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer"
        )
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_support")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE public.platform_email_outbox "
        "TO aurum_support"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM aurum_mailer")
