"""notifications: notification, notification_subscription, notification_delivery

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-21

- notification: tenant-scoped, RLS on; the in-app inbox.
- notification_subscription: user-scoped settings (which event types
  fire on which channels). Tied to app_user, not to a tenant — a
  developer working across tenants has one preference set.
- notification_delivery: per-channel outbox row, INSERT once and
  updated by the Celery worker as it tries to send/retry.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- notification -------------------------------------------------------
    op.execute(
        """
        CREATE TABLE notification (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          user_id     UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
          event_type  TEXT NOT NULL,
          title       TEXT NOT NULL,
          body        TEXT,
          data        JSONB,
          severity    TEXT NOT NULL DEFAULT 'info'
                        CHECK (severity IN ('info','warning','error','critical')),
          read_at     TIMESTAMPTZ,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_notification_user_unread ON notification "
        "(user_id, created_at DESC) WHERE read_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_notification_tenant ON notification "
        "(tenant_id, created_at DESC)"
    )
    op.execute("ALTER TABLE notification ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON notification
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- notification_subscription -----------------------------------------
    op.execute(
        """
        CREATE TABLE notification_subscription (
          user_id     UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
          event_type  TEXT NOT NULL,
          channels    JSONB NOT NULL DEFAULT '["in_app"]'::jsonb,
          is_enabled  BOOLEAN NOT NULL DEFAULT true,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, event_type)
        )
        """
    )

    # ---- notification_delivery ---------------------------------------------
    op.execute(
        """
        CREATE TABLE notification_delivery (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          notification_id UUID NOT NULL REFERENCES notification(id) ON DELETE CASCADE,
          channel         TEXT NOT NULL CHECK (channel IN ('email','telegram','sms')),
          recipient       TEXT NOT NULL,
          status          TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','sent','failed','bounced')),
          error_message   TEXT,
          attempts        INT NOT NULL DEFAULT 0,
          sent_at         TIMESTAMPTZ,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_nd_status ON notification_delivery (status, created_at) "
        "WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX ix_nd_notification ON notification_delivery (notification_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notification_delivery CASCADE")
    op.execute("DROP TABLE IF EXISTS notification_subscription CASCADE")
    op.execute("DROP TABLE IF EXISTS notification CASCADE")
