"""
RAG 文档管理 API
"""
import os
import tempfile
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, BackgroundTasks, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc

from app.api import deps
from app.model.user import User
from app.model.knowledge_base import KnowledgeBase, Document, DocumentChunk
from app.schema.knowledge_base import (
    DocumentResponse,
    DocumentListResponse,
    DocumentChunkResponse,
    DocumentChunkListResponse
)
from app.service.document_service import document_service
from app.service.audit_service import write_audit_log, write_system_audit_log
from app.rag.storage_manager import storage
from app.core.logger import logger
from app.api.rag_permissions import can_manage_knowledge_base, rag_audit_details

router = APIRouter()


@router.post("/knowledge-bases/{kb_id}/documents", response_model=DocumentResponse)
async def upload_document(
    kb_id: int,
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    上传文档到知识库

    - 支持的文件类型: PDF, TXT, MD
    - 文件会先上传到 MinIO
    - 然后在后台处理（分块、向量化）
    - 使用文件哈希去重
    """
    # 验证知识库所有权
    kb_query = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    kb_result = await db.execute(kb_query)
    kb = kb_result.scalars().first()

    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if not can_manage_knowledge_base(current_user, kb):
        raise HTTPException(status_code=403, detail="Only knowledge base owner or admin can upload documents")

    # 检查文件类型
    file_type = document_service.get_file_type(file.filename)
    if file_type == 'unknown':
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Supported: PDF, TXT, MD"
        )

    # 保存临时文件
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1])
    try:
        # 写入临时文件
        content = await file.read()
        temp_file.write(content)
        temp_file.close()

        file_size = len(content)

        # 计算文件哈希
        file_hash = document_service.calculate_file_hash(temp_file.name)

        # 检查是否已存在
        existing_doc = await document_service.check_duplicate(db, kb_id, file_hash)
        if existing_doc:
            os.unlink(temp_file.name)
            raise HTTPException(
                status_code=400,
                detail=f"File already exists in this knowledge base: {existing_doc.filename}"
            )

        # 上传到 MinIO
        try:
            minio_url = storage.upload_file(
                str(kb.user_id),
                kb.name,
                temp_file.name,
                object_name=f"{kb.user_id}/{kb.name}/{file_hash}_{file.filename}"
            )
        except Exception as e:
            os.unlink(temp_file.name)
            raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

        # 创建文档记录
        document = await document_service.create_document_record(
            db=db,
            kb_id=kb_id,
            filename=file.filename,
            file_hash=file_hash,
            file_size=file_size,
            file_type=file_type,
            minio_url=minio_url
        )
        await write_audit_log(
            db,
            current_user=current_user,
            request=request,
            event_type="document_upload",
            status="success",
            details=rag_audit_details(
                current_user,
                kb,
                kb_id=kb_id,
                kb_name=kb.name,
                doc_id=document.id,
                filename=document.filename,
                file_hash=file_hash,
                file_size=file_size,
                file_type=file_type,
                minio_url=minio_url,
            ),
        )

        # 后台处理文档
        background_tasks.add_task(
            process_document_background,
            db,
            document.id,
            temp_file.name
        )

        return document

    except HTTPException:
        raise
    except Exception as e:
        if os.path.exists(temp_file.name):
            os.unlink(temp_file.name)
        logger.error(f"Error uploading document: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


async def process_document_background(db: AsyncSession, document_id: int, temp_file_path: str):
    """后台任务：处理文档"""
    try:
        # 重新获取文档
        query = select(Document).where(Document.id == document_id)
        result = await db.execute(query)
        document = result.scalars().first()

        if not document:
            logger.error(f"Document {document_id} not found")
            return

        # 处理文档
        success, message = await document_service.process_document(db, document, temp_file_path)
        kb_result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == document.kb_id))
        kb = kb_result.scalars().first()
        await write_system_audit_log(
            user_id=str(kb.user_id) if kb else "unknown",
            user_role="unknown",
            event_type="document_process",
            status="success" if success else "failed",
            details={
                "kb_id": document.kb_id,
                "doc_id": document.id,
                "filename": document.filename,
                "chunk_count": document.chunk_count,
                "message": message,
            },
            error_message=None if success else message,
        )

        if success:
            logger.info(f"Document {document_id} processed successfully")
        else:
            logger.error(f"Document {document_id} processing failed: {message}")

    except Exception as e:
        logger.error(f"Error in background processing: {e}", exc_info=True)
    finally:
        # 删除临时文件
        if os.path.exists(temp_file_path):
            os.unlink(temp_file_path)


@router.get("/knowledge-bases/{kb_id}/documents", response_model=DocumentListResponse)
async def list_documents(
    kb_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None, description="过滤状态: pending, processing, completed, failed"),
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    获取知识库的文档列表

    - 支持分页
    - 支持按状态过滤
    - 按创建时间倒序排列
    """
    # 验证知识库所有权
    kb_query = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    kb_result = await db.execute(kb_query)
    kb = kb_result.scalars().first()

    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # 构建查询
    query = select(Document).where(Document.kb_id == kb_id)

    # 状态过滤
    if status:
        query = query.where(Document.status == status)

    # 排序和分页
    query = query.order_by(desc(Document.created_at)).offset(skip).limit(limit)

    result = await db.execute(query)
    documents = result.scalars().all()

    # 统计总数
    count_query = select(func.count(Document.id)).where(Document.kb_id == kb_id)
    if status:
        count_query = count_query.where(Document.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    return DocumentListResponse(
        documents=[DocumentResponse.model_validate(doc) for doc in documents],
        total=total
    )


@router.get("/knowledge-bases/{kb_id}/documents/{doc_id}", response_model=DocumentResponse)
async def get_document(
    kb_id: int,
    doc_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """获取单个文档详情"""
    # 验证知识库所有权
    kb_query = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    kb_result = await db.execute(kb_query)
    kb = kb_result.scalars().first()

    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # 获取文档
    doc_query = select(Document).where(
        Document.id == doc_id,
        Document.kb_id == kb_id
    )
    doc_result = await db.execute(doc_query)
    document = doc_result.scalars().first()

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    return document


@router.delete("/knowledge-bases/{kb_id}/documents/{doc_id}")
async def delete_document(
    kb_id: int,
    doc_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    删除文档

    - 会级联删除所有分块
    - 会删除 MinIO 中的文件（TODO）
    """
    # 验证知识库所有权
    kb_query = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    kb_result = await db.execute(kb_query)
    kb = kb_result.scalars().first()

    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if not can_manage_knowledge_base(current_user, kb):
        raise HTTPException(status_code=403, detail="Only knowledge base owner or admin can delete documents")

    # 获取文档
    doc_query = select(Document).where(
        Document.id == doc_id,
        Document.kb_id == kb_id
    )
    doc_result = await db.execute(doc_query)
    document = doc_result.scalars().first()

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    deleted_document = rag_audit_details(
        current_user,
        kb,
        kb_id=kb_id,
        kb_name=kb.name,
        doc_id=document.id,
        filename=document.filename,
        file_hash=document.file_hash,
        file_size=document.file_size,
        file_type=document.file_type,
        chunk_count=document.chunk_count,
        status=document.status,
    )

    # 删除文档
    success, message = await document_service.delete_document(db, document)

    if not success:
        await write_audit_log(
            db,
            current_user=current_user,
            request=request,
            event_type="document_delete",
            status="failed",
            details=deleted_document,
            error_message=message,
        )
        raise HTTPException(status_code=500, detail=message)
    await write_audit_log(
        db,
        current_user=current_user,
        request=request,
        event_type="document_delete",
        status="success",
        details={**deleted_document, "message": message},
    )

    return {
        "message": "Document deleted successfully",
        "doc_id": doc_id,
        "details": message
    }


@router.get("/knowledge-bases/{kb_id}/documents/{doc_id}/chunks", response_model=DocumentChunkListResponse)
async def list_document_chunks(
    kb_id: int,
    doc_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    获取文档的分块列表（预览）

    - 支持分页
    - 按 chunk_index 排序
    - 用于查看文档是如何被分块的
    """
    # 验证知识库所有权
    kb_query = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    kb_result = await db.execute(kb_query)
    kb = kb_result.scalars().first()

    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # 验证文档存在
    doc_query = select(Document).where(
        Document.id == doc_id,
        Document.kb_id == kb_id
    )
    doc_result = await db.execute(doc_query)
    document = doc_result.scalars().first()

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # 获取分块
    chunk_query = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == doc_id)
        .order_by(DocumentChunk.chunk_index)
        .offset(skip)
        .limit(limit)
    )
    chunk_result = await db.execute(chunk_query)
    chunks = chunk_result.scalars().all()

    # 统计总数
    count_query = select(func.count(DocumentChunk.id)).where(
        DocumentChunk.document_id == doc_id
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    return DocumentChunkListResponse(
        chunks=[DocumentChunkResponse.model_validate(chunk) for chunk in chunks],
        total=total
    )
