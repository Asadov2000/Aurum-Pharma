"""Supplier return documents are immutable financial inventory records."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_supplier_return_is_append_only(maintenance_engine: AsyncEngine) -> None:
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            suffix = uuid4().hex
            tenant_id = (
                await connection.execute(
                    text(
                        "INSERT INTO tenant (name, contact_email) "
                        "VALUES (:name, :email) RETURNING id"
                    ),
                    {
                        "name": f"Immutable supplier return {suffix}",
                        "email": f"immutable-return-{suffix}@example.invalid",
                    },
                )
            ).scalar_one()
            await connection.execute(
                text("ALTER TABLE branch DISABLE TRIGGER trg_branch_sync_writer")
            )
            branch_id = (
                await connection.execute(
                    text(
                        "INSERT INTO branch (tenant_id, name) "
                        "VALUES (:tenant_id, 'Main') RETURNING id"
                    ),
                    {"tenant_id": tenant_id},
                )
            ).scalar_one()
            await connection.execute(
                text("ALTER TABLE branch ENABLE TRIGGER trg_branch_sync_writer")
            )
            supplier_id = (
                await connection.execute(
                    text(
                        "INSERT INTO supplier (tenant_id, name) "
                        "VALUES (:tenant_id, 'Supplier') RETURNING id"
                    ),
                    {"tenant_id": tenant_id},
                )
            ).scalar_one()
            catalog_id = (
                await connection.execute(
                    text(
                        "INSERT INTO tenant_catalog (tenant_id, brand_name) "
                        "VALUES (:tenant_id, 'Return item') RETURNING id"
                    ),
                    {"tenant_id": tenant_id},
                )
            ).scalar_one()
            await connection.execute(
                text(
                    "ALTER TABLE incoming_document "
                    "DISABLE TRIGGER trg_incoming_document_writer_guard"
                )
            )
            await connection.execute(
                text(
                    "ALTER TABLE incoming_document "
                    "DISABLE TRIGGER trg_guard_incoming_document_lifecycle"
                )
            )
            document_id = (
                await connection.execute(
                    text(
                        "INSERT INTO incoming_document ("
                        "tenant_id, branch_id, supplier_id, operation_id, document_date, status"
                        ") VALUES ("
                        ":tenant_id, :branch_id, :supplier_id, :operation_id, "
                        "CURRENT_DATE, 'accepted'"
                        ") RETURNING id"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "branch_id": branch_id,
                        "supplier_id": supplier_id,
                        "operation_id": uuid4(),
                    },
                )
            ).scalar_one()
            await connection.execute(
                text(
                    "ALTER TABLE incoming_document "
                    "ENABLE TRIGGER trg_incoming_document_writer_guard"
                )
            )
            await connection.execute(
                text(
                    "ALTER TABLE incoming_document "
                    "ENABLE TRIGGER trg_guard_incoming_document_lifecycle"
                )
            )
            await connection.execute(
                text("ALTER TABLE batch DISABLE TRIGGER trg_batch_writer_guard")
            )
            batch_id = (
                await connection.execute(
                    text(
                        "INSERT INTO batch ("
                        "tenant_id, branch_id, catalog_id, expires_at, purchase_price, "
                        "sale_price, qty_initial, qty_remaining"
                        ") VALUES ("
                        ":tenant_id, :branch_id, :catalog_id, CURRENT_DATE + 365, 5, 10, 5, 5"
                        ") RETURNING id"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "branch_id": branch_id,
                        "catalog_id": catalog_id,
                    },
                )
            ).scalar_one()
            await connection.execute(
                text("ALTER TABLE batch ENABLE TRIGGER trg_batch_writer_guard")
            )
            return_id = (
                await connection.execute(
                    text(
                        "INSERT INTO supplier_return ("
                        "tenant_id, supplier_id, source_document_id, batch_id, qty, amount, reason"
                        ") VALUES ("
                        ":tenant_id, :supplier_id, :document_id, :batch_id, 1, 5, 'damaged'"
                        ") RETURNING id"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "supplier_id": supplier_id,
                        "document_id": document_id,
                        "batch_id": batch_id,
                    },
                )
            ).scalar_one()

            update_savepoint = await connection.begin_nested()
            with pytest.raises(DBAPIError, match="Supplier return ledger is immutable"):
                await connection.execute(
                    text("UPDATE supplier_return SET qty = 2 WHERE id = :id"),
                    {"id": return_id},
                )
            await update_savepoint.rollback()

            delete_savepoint = await connection.begin_nested()
            with pytest.raises(DBAPIError, match="Supplier return ledger is immutable"):
                await connection.execute(
                    text("DELETE FROM supplier_return WHERE id = :id"),
                    {"id": return_id},
                )
            await delete_savepoint.rollback()
        finally:
            await transaction.rollback()
