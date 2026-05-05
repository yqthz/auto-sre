import asyncio
from typing import Dict

from app.core.logger import logger
from app.core.config import settings
from app.service.audit_service import audit_log_from_entry
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import sessionmaker


_audit_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,
    poolclass=NullPool,
)
_AuditSessionLocal = sessionmaker(
    _audit_engine, class_=AsyncSession, expire_on_commit=False
)


async def _append_audit_async(entry: Dict):
    async with _AuditSessionLocal() as db:
        db.add(audit_log_from_entry(entry))
        await db.commit()


def append_audit(entry: Dict):
    """Persist one audit record into database."""

    async def _run():
        try:
            await _append_audit_async(entry)
        except Exception as exc:
            logger.error("append_audit db write failed: %s", exc, exc_info=True)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_run())
        return

    loop.create_task(_run())
