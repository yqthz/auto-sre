import asyncio
from typing import Dict

from app.core.logger import logger
from app.db.session import AsyncSessionLocal
from app.service.audit_service import audit_log_from_entry


async def _append_audit_async(entry: Dict):
    async with AsyncSessionLocal() as db:
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
