"""
RAG 文档处理服务
处理文档上传、分块、向量化等核心逻辑
"""
import os
import hashlib
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.model.knowledge_base import KnowledgeBase, Document, DocumentChunk
from app.rag.text_processor import text_processor
from app.rag.storage_manager import storage
from app.utils.llm_utils import get_embeddings
from app.core.logger import logger


class DocumentService:
    """文档处理服务"""

    def __init__(self):
        self.embeddings = get_embeddings()

    def calculate_file_hash(self, file_path: str) -> str:
        """计算文件 MD5 哈希"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def get_file_type(self, filename: str) -> str:
        """获取文件类型"""
        ext = os.path.splitext(filename)[1].lower()
        type_map = {
            '.pdf': 'pdf',
            '.txt': 'txt',
            '.md': 'md',
            '.docx': 'docx',
            '.doc': 'doc'
        }
        return type_map.get(ext, 'unknown')

    async def check_duplicate(
        self,
        db: AsyncSession,
        kb_id: int,
        file_hash: str
    ) -> Optional[Document]:
        """检查文件是否已存在（去重）"""
        query = select(Document).where(
            Document.kb_id == kb_id,
            Document.file_hash == file_hash
        )
        result = await db.execute(query)
        return result.scalars().first()

    async def create_document_record(
        self,
        db: AsyncSession,
        kb_id: int,
        filename: str,
        file_hash: str,
        file_size: int,
        file_type: str,
        minio_url: str
    ) -> Document:
        """创建文档记录"""
        document = Document(
            kb_id=kb_id,
            filename=filename,
            file_hash=file_hash,
            file_size=file_size,
            file_type=file_type,
            minio_url=minio_url,
            status="pending",
            chunk_count=0
        )
        db.add(document)
        await db.commit()
        await db.refresh(document)
        return document

    async def process_document(
        self,
        db: AsyncSession,
        document: Document,
        local_file_path: str
    ) -> Tuple[bool, str]:
        """
        处理文档：读取、分块、向量化、存储

        返回: (成功标志, 消息)
        """
        try:
            # 更新状态为处理中
            document.status = "processing"
            await db.commit()

            # 1. 读取文件内容
            logger.info(f"Reading file: {document.filename}")
            try:
                content = text_processor.read_file(local_file_path, document.filename)
            except Exception as e:
                raise ValueError(f"Failed to read file: {str(e)}")

            if not content or len(content.strip()) == 0:
                raise ValueError("File is empty or contains no text")

            # 2. 分块
            logger.info(f"Splitting text into chunks")
            chunks = text_processor.split(content)

            if not chunks:
                raise ValueError("No chunks generated from file")

            logger.info(f"Generated {len(chunks)} chunks")

            # 2.1 保存全文文本（主读取路径）
            document.content_text = content

            # 3. 向量化
            logger.info(f"Generating embeddings for {len(chunks)} chunks")
            try:
                embeddings_list = self.embeddings.embed_documents(chunks)
            except Exception as e:
                raise ValueError(f"Failed to generate embeddings: {str(e)}")

            # 4. 批量插入分块
            logger.info(f"Inserting chunks into database")
            for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings_list)):
                chunk = DocumentChunk(
                    document_id=document.id,
                    chunk_index=i,
                    content=chunk_text,
                    embedding=embedding,
                    metadata_=f'{{"chunk_index": {i}, "source": "{document.filename}"}}'
                )
                db.add(chunk)

            # 5. 更新文档状态
            document.status = "completed"
            document.chunk_count = len(chunks)
            await db.commit()

            # 6. 更新知识库统计
            await self.update_kb_stats(db, document.kb_id)

            logger.info(f"Document processed successfully: {document.filename}")
            return True, f"Successfully processed {len(chunks)} chunks"

        except Exception as e:
            logger.error(f"Error processing document {document.filename}: {e}", exc_info=True)

            # 更新状态为失败
            document.status = "failed"
            document.error_message = str(e)
            await db.commit()

            return False, f"Processing failed: {str(e)}"

    async def update_kb_stats(self, db: AsyncSession, kb_id: int):
        """更新知识库统计信息"""
        # 统计文档数量
        from sqlalchemy import func
        doc_count_query = select(func.count(Document.id)).where(Document.kb_id == kb_id)
        doc_count_result = await db.execute(doc_count_query)
        doc_count = doc_count_result.scalar()

        # 统计分块数量
        chunk_count_query = select(func.sum(Document.chunk_count)).where(Document.kb_id == kb_id)
        chunk_count_result = await db.execute(chunk_count_query)
        chunk_count = chunk_count_result.scalar() or 0

        # 更新知识库
        kb_query = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        kb_result = await db.execute(kb_query)
        kb = kb_result.scalars().first()

        if kb:
            kb.document_count = doc_count
            kb.chunk_count = chunk_count
            await db.commit()

    async def delete_document(
        self,
        db: AsyncSession,
        document: Document
    ) -> Tuple[bool, str]:
        """
        删除文档

        - 删除数据库记录（级联删除分块）
        - 删除 MinIO 文件（TODO）
        """
        try:
            kb_id = document.kb_id
            chunk_count = document.chunk_count

            # 删除文档（级联删除分块）
            await db.delete(document)
            await db.commit()

            # 更新知识库统计
            await self.update_kb_stats(db, kb_id)

            # TODO: 删除 MinIO 文件
            # storage.delete_file(document.minio_url)

            logger.info(f"Document deleted: {document.filename}")
            return True, f"Deleted document and {chunk_count} chunks"

        except Exception as e:
            logger.error(f"Error deleting document: {e}", exc_info=True)
            return False, f"Delete failed: {str(e)}"


# 全局单例
document_service = DocumentService()
