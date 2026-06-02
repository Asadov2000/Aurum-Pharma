"""tenant_settings.report_timezone — interpret report dates in the pharmacy's
local time, not UTC.

Date-range report filters (sales summary, stock-on-date, receipt search) cast
timestamptz to a calendar date; without a timezone that cast is in UTC, so a
sale at 00:00–04:59 in Dushanbe (UTC+5) lands in the previous day/month. The
column holds an IANA zone (default 'Asia/Dushanbe', no DST) used as
(ts AT TIME ZONE report_timezone)::date in those queries.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenant_settings "
        "ADD COLUMN report_timezone TEXT NOT NULL DEFAULT 'Asia/Dushanbe'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tenant_settings DROP COLUMN IF EXISTS report_timezone")
