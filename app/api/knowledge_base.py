"""
RAG 知识库管理 API
"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc

from app.api import deps
from app.model.user import User
from app.model.knowledge_base import KnowledgeBase, Document
from app.schema.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    KnowledgeBaseResponse,
    KnowledgeBaseListResponse
)
from app.service.audit_service import write_audit_log

router = APIRouter()


@router.post("/knowledge-bases", response_model=KnowledgeBaseResponse)
async def create_knowledge_base(
    kb_in: KnowledgeBaseCreate,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    创建知识库

    - 每个用户可以创建多个知识库
    - 知识库名称在用户范围内不能重复
    """
    # 检查名称是否已存在
    query = select(KnowledgeBase).where(
        KnowledgeBase.user_id == current_user.id,
        KnowledgeBase.name == kb_in.name
    )
    result = await db.execute(query)
    existing_kb = result.scalars().first()

    if existing_kb:
        raise HTTPException(
            status_code=400,
            detail=f"Knowledge base with name '{kb_in.name}' already exists"
        )

    # 创建知识库
    kb = KnowledgeBase(
        name=kb_in.name,
        description=kb_in.description,
        user_id=current_user.id,
        document_count=0,
        chunk_count=0
    )

    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    await write_audit_log(
        db,
        current_user=current_user,
        request=request,
        event_type="knowledge_base_create",
        status="success",
        details={
            "kb_id": kb.id,
            "name": kb.name,
            "description": kb.description,
        },
    )

    return kb


@router.get("/knowledge-bases", response_model=KnowledgeBaseListResponse)
async def list_knowledge_bases(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None, description="搜索知识库名称"),
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    获取知识库列表

    - 支持分页
    - 支持按名称搜索
    - 按更新时间倒序排列
    """
    # 构建查询
    query = select(KnowledgeBase).where(KnowledgeBase.user_id == current_user.id)

    # 搜索过滤
    if search:
        query = query.where(KnowledgeBase.name.ilike(f"%{search}%"))

    # 排序和分页
    query = query.order_by(desc(KnowledgeBase.updated_at)).offset(skip).limit(limit)

    result = await db.execute(query)
    kbs = result.scalars().all()

    # 统计总数
    count_query = select(func.count(KnowledgeBase.id)).where(
        KnowledgeBase.user_id == current_user.id
    )
    if search:
        count_query = count_query.where(KnowledgeBase.name.ilike(f"%{search}%"))

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    return KnowledgeBaseListResponse(
        knowledge_bases=[KnowledgeBaseResponse.model_validate(kb) for kb in kbs],
        total=total
    )


@router.get("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    kb_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """获取单个知识库详情"""
    query = select(KnowledgeBase).where(
        KnowledgeBase.id == kb_id,
        KnowledgeBase.user_id == current_user.id
    )
    result = await db.execute(query)
    kb = result.scalars().first()

    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    return kb


@router.patch("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    kb_id: int,
    kb_update: KnowledgeBaseUpdate,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    更新知识库

    - 可以更新名称、描述
    - 如果更新名称，需要检查是否重复
    """
    # 获取知识库
    query = select(KnowledgeBase).where(
        KnowledgeBase.id == kb_id,
        KnowledgeBase.user_id == current_user.id
    )
    result = await db.execute(query)
    kb = result.scalars().first()

    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    old_data = {
        "name": kb.name,
        "description": kb.description,
    }

    # 检查名称是否重复
    if kb_update.name and kb_update.name != kb.name:
        check_query = select(KnowledgeBase).where(
            KnowledgeBase.user_id == current_user.id,
            KnowledgeBase.name == kb_update.name,
            KnowledgeBase.id != kb_id
        )
        check_result = await db.execute(check_query)
        if check_result.scalars().first():
            raise HTTPException(
                status_code=400,
                detail=f"Knowledge base with name '{kb_update.name}' already exists"
            )

    # 更新字段
    if kb_update.name is not None:
        kb.name = kb_update.name
    if kb_update.description is not None:
        kb.description = kb_update.description

    kb.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(kb)
    await write_audit_log(
        db,
        current_user=current_user,
        request=request,
        event_type="knowledge_base_update",
        status="success",
        details={
            "kb_id": kb.id,
            "old_data": old_data,
            "new_data": {
                "name": kb.name,
                "description": kb.description,
            },
        },
    )

    return kb


@router.delete("/knowledge-bases/{kb_id}")
async def delete_knowledge_base(
    kb_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    删除知识库

    - 会级联删除所有文档和分块
    - 需要同时删除 MinIO 中的文件（TODO）
    """
    # 获取知识库
    query = select(KnowledgeBase).where(
        KnowledgeBase.id == kb_id,
        KnowledgeBase.user_id == current_user.id
    )
    result = await db.execute(query)
    kb = result.scalars().first()

    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # 统计信息（用于返回）
    doc_count = kb.document_count
    deleted_kb = {
        "kb_id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "document_count": kb.document_count,
        "chunk_count": kb.chunk_count,
    }

    # 删除知识库（级联删除文档和分块）
    await db.delete(kb)
    await db.commit()
    await write_audit_log(
        db,
        current_user=current_user,
        request=request,
        event_type="knowledge_base_delete",
        status="success",
        details=deleted_kb,
    )

    return {
        "message": "Knowledge base deleted successfully",
        "kb_id": kb_id,
        "deleted_documents": doc_count
    }


@router.get("/knowledge-bases/{kb_id}/stats")
async def get_knowledge_base_stats(
    kb_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    获取知识库统计信息

    - 文档数量
    - 分块数量
    - 总文件大小
    - 文档类型分布
    """
    # 验证知识库所有权
    kb_query = select(KnowledgeBase).where(
        KnowledgeBase.id == kb_id,
        KnowledgeBase.user_id == current_user.id
    )
    kb_result = await db.execute(kb_query)
    kb = kb_result.scalars().first()

    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # 统计文档数量
    doc_count_query = select(func.count(Document.id)).where(Document.kb_id == kb_id)
    doc_count_result = await db.execute(doc_count_query)
    doc_count = doc_count_result.scalar()

    # 统计总文件大小
    size_query = select(func.sum(Document.file_size)).where(Document.kb_id == kb_id)
    size_result = await db.execute(size_query)
    total_size = size_result.scalar() or 0

    # 统计文档类型分布
    type_query = select(
        Document.file_type,
        func.count(Document.id).label("count")
    ).where(Document.kb_id == kb_id).group_by(Document.file_type)
    type_result = await db.execute(type_query)
    type_distribution = {row[0] or "unknown": row[1] for row in type_result}

    # 统计处理状态
    status_query = select(
        Document.status,
        func.count(Document.id).label("count")
    ).where(Document.kb_id == kb_id).group_by(Document.status)
    status_result = await db.execute(status_query)
    status_distribution = {row[0]: row[1] for row in status_result}

    return {
        "kb_id": kb_id,
        "kb_name": kb.name,
        "document_count": doc_count,
        "chunk_count": kb.chunk_count,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "file_type_distribution": type_distribution,
        "status_distribution": status_distribution,
        "created_at": kb.created_at,
        "updated_at": kb.updated_at
    }
