"""
数据库初始化脚本
"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings
from app.model.user import Base, User
from app.model.audit_log import AuditLog
from app.model.chat import ChatSession, ChatMessage
from app.model.knowledge_base import KnowledgeBase, Document, DocumentChunk
from app.core.logger import logger


async def init_db():
    """初始化数据库表"""
    logger.info("Creating database tables...")

    engine = create_async_engine(settings.DATABASE_URL, echo=True)

    async with engine.begin() as conn:
        # 创建所有表
        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()
    logger.info("Database tables created successfully!")


if __name__ == "__main__":
    asyncio.run(init_db())
