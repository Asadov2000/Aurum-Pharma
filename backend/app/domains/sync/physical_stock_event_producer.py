"""Optional early completion of DB-captured stock facts; the caller owns commit."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError


async def produce_physical_stock_events(session: AsyncSession) -> None:
    """Validate and emit pending source captures without committing or delivery.

    Source triggers capture their owner-controlled gate, writer epoch and source
    transaction. PostgreSQL also runs this completeness trigger at commit, so a
    missed Python hook cannot create a committed warehouse change without its
    required event. Existing sources never acquire retroactive captures.
    """
    if session.new or session.dirty or session.deleted:
        raise ConflictError("Physical event completion requires a clean session")
    await session.execute(text("SET CONSTRAINTS trg_physical_stock_source_complete IMMEDIATE"))
    await session.execute(text("SET CONSTRAINTS trg_physical_stock_source_complete DEFERRED"))
