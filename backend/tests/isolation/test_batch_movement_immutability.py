"""The stock movement ledger is append-only for every runtime path."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_batch_movement_is_append_only(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            privileges = (await connection.execute(text("""
                        SELECT
                          pg_catalog.has_table_privilege(
                            role_name,
                            'public.batch_movement',
                            'SELECT'
                          ) AS can_select,
                          pg_catalog.has_table_privilege(
                            role_name,
                            'public.batch_movement',
                            'INSERT'
                          ) AS can_insert,
                          pg_catalog.has_table_privilege(
                            role_name,
                            'public.batch_movement',
                            'UPDATE'
                          ) AS can_update,
                          pg_catalog.has_table_privilege(
                            role_name,
                            'public.batch_movement',
                            'DELETE'
                          ) AS can_delete,
                          role_name
                        FROM (
                          VALUES ('aurum_app'), ('aurum_support')
                        ) AS roles(role_name)
                        ORDER BY role_name
                    """))).mappings().all()
            assert [dict(row) for row in privileges] == [
                {
                    "can_select": True,
                    "can_insert": True,
                    "can_update": False,
                    "can_delete": False,
                    "role_name": "aurum_app",
                },
                {
                    "can_select": True,
                    "can_insert": True,
                    "can_update": False,
                    "can_delete": False,
                    "role_name": "aurum_support",
                },
            ]

            suffix = uuid4().hex
            tenant_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.tenant (name, contact_email)
                        VALUES (:name, :email)
                        RETURNING id
                    """),
                    {
                        "name": f"Immutable ledger {suffix}",
                        "email": f"immutable-ledger-{suffix}@example.invalid",
                    },
                )
            ).scalar_one()
            await connection.execute(
                text("ALTER TABLE public.branch " "DISABLE TRIGGER trg_branch_sync_writer")
            )
            branch_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.branch (tenant_id, name)
                        VALUES (:tenant_id, 'Main')
                        RETURNING id
                    """),
                    {"tenant_id": tenant_id},
                )
            ).scalar_one()
            await connection.execute(
                text("ALTER TABLE public.branch " "ENABLE TRIGGER trg_branch_sync_writer")
            )
            catalog_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.tenant_catalog (tenant_id, brand_name)
                        VALUES (:tenant_id, 'Ledger test item')
                        RETURNING id
                    """),
                    {"tenant_id": tenant_id},
                )
            ).scalar_one()
            await connection.execute(
                text("ALTER TABLE public.batch " "DISABLE TRIGGER trg_batch_writer_guard")
            )
            batch_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.batch (
                          tenant_id,
                          branch_id,
                          catalog_id,
                          expires_at,
                          purchase_price,
                          sale_price,
                          qty_initial,
                          qty_remaining
                        )
                        VALUES (
                          :tenant_id,
                          :branch_id,
                          :catalog_id,
                          CURRENT_DATE + 30,
                          1,
                          2,
                          5,
                          0
                        )
                        RETURNING id
                    """),
                    {
                        "tenant_id": tenant_id,
                        "branch_id": branch_id,
                        "catalog_id": catalog_id,
                    },
                )
            ).scalar_one()
            movement_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.batch_movement (
                          tenant_id,
                          batch_id,
                          movement_type,
                          qty_delta
                        )
                        VALUES (:tenant_id, :batch_id, 'incoming', 5)
                        RETURNING id
                    """),
                    {"tenant_id": tenant_id, "batch_id": batch_id},
                )
            ).scalar_one()
            await connection.execute(
                text("ALTER TABLE public.batch " "ENABLE TRIGGER trg_batch_writer_guard")
            )

            update_savepoint = await connection.begin_nested()
            with pytest.raises(DBAPIError, match="Batch movement ledger is immutable"):
                await connection.execute(
                    text("""
                        UPDATE public.batch_movement
                        SET qty_delta = 500
                        WHERE id = :movement_id
                    """),
                    {"movement_id": movement_id},
                )
            await update_savepoint.rollback()

            delete_savepoint = await connection.begin_nested()
            with pytest.raises(DBAPIError, match="Batch movement ledger is immutable"):
                await connection.execute(
                    text("""
                        DELETE FROM public.batch_movement
                        WHERE id = :movement_id
                    """),
                    {"movement_id": movement_id},
                )
            await delete_savepoint.rollback()

            movement_qty = (
                await connection.execute(
                    text("""
                        SELECT qty_delta
                        FROM public.batch_movement
                        WHERE id = :movement_id
                    """),
                    {"movement_id": movement_id},
                )
            ).scalar_one()
            batch_qty = (
                await connection.execute(
                    text("SELECT qty_remaining FROM public.batch WHERE id = :batch_id"),
                    {"batch_id": batch_id},
                )
            ).scalar_one()
            assert str(movement_qty) == "5.000"
            assert str(batch_qty) == "5.000"
        finally:
            await transaction.rollback()
