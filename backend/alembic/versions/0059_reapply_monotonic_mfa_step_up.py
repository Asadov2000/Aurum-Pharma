"""Reapply monotonic access-token-only MFA step-up timestamps.

Revision ID: 0059
Revises: 0058
Create Date: 2026-07-18
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Union

from alembic import op

revision: str = "0059"
down_revision: Union[str, Sequence[str], None] = "0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _load_revision_module(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"aurum_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration source: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    source_0057 = _load_revision_module("0057_harden_support_mfa.py")
    statement = source_0057.COMPLETE_STEP_UP_SQL.replace(
        "CREATE FUNCTION public.complete_support_mfa_step_up",
        "CREATE OR REPLACE FUNCTION public.complete_support_mfa_step_up",
        1,
    )
    op.execute(statement)
    signature = "public.complete_support_mfa_step_up(" "UUID, UUID, BIGINT, TEXT, JSONB)"
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_support")


def downgrade() -> None:
    # The corrected function is also the canonical state of revision 0057.
    pass
