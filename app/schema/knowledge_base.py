"""
RAG 知识库相关 Schema
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# ============ 知识库 Schema ============

class KnowledgeBaseCreate(BaseModel):
    """创建知识库请求"""
    name: str = Field(..., min_length=1, max_length=255, description="知识库名称")
    description: Optional[str] = Field(None, description="知识库描述")
    is_public: bool = Field(False, description="是否公开")


class KnowledgeBaseUpdate(BaseModel):
    """更新知识库请求"""
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="知识库名称")
    description: Optional[str] = Field(None, description="知识库描述")
    is_public: Optional[bool] = Field(None, description="是否公开")


class KnowledgeBaseResponse(BaseModel):
    """知识库响应"""
    id: int
    name: str
    description: Optional[str]
    user_id: int
    is_public: bool
    created_at: datetime
    updated_at: datetime
    document_count: int
    chunk_count: int

    class Config:
        from_attributes = True


class KnowledgeBaseListResponse(BaseModel):
    """知识库列表响应"""
    knowledge_bases: List[KnowledgeBaseResponse]
    total: int


# ============ 文档 Schema ============

class DocumentResponse(BaseModel):
    """文档响应"""
    id: int
    kb_id: int
    filename: str
    file_hash: str
    file_size: Optional[int]
    file_type: Optional[str]
    status: str
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    chunk_count: int

    class Config:
        from_attributes = True


class DocumentListResponse(BaseModel):
    """文档列表响应"""
    documents: List[DocumentResponse]
    total: int


# ============ 文档分块 Schema ============

class DocumentChunkResponse(BaseModel):
    """文档分块响应"""
    id: int
    document_id: int
    chunk_index: int
    content: str
    metadata: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentChunkListResponse(BaseModel):
    """文档分块列表响应"""
    chunks: List[DocumentChunkResponse]
    total: int


# ============ 搜索 Schema ============

class SearchRequest(BaseModel):
    """搜索请求"""
    query: str = Field(..., min_length=1, description="搜索查询")
    kb_id: Optional[int] = Field(None, description="指定知识库 ID（可选）")
    top_k: int = Field(5, ge=1, le=20, description="返回结果数量")


class SearchResult(BaseModel):
    """搜索结果"""
    content: str
    metadata: Optional[dict]
    score: float
    document_id: int
    filename: str


class SearchResponse(BaseModel):
    """搜索响应"""
    query: str
    results: List[SearchResult]
    total: int
