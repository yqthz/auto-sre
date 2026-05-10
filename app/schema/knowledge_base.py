"""
RAG knowledge base schemas.
"""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Knowledge base name")
    description: Optional[str] = Field(None, description="Knowledge base description")


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Knowledge base name")
    description: Optional[str] = Field(None, description="Knowledge base description")


class KnowledgeBaseResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    user_id: int
    owner_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    document_count: int
    chunk_count: int

    class Config:
        from_attributes = True


class KnowledgeBaseListResponse(BaseModel):
    knowledge_bases: List[KnowledgeBaseResponse]
    total: int


class DocumentResponse(BaseModel):
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


class DocumentPreviewResponse(BaseModel):
    preview_text: str


class DocumentListResponse(BaseModel):
    documents: List[DocumentResponse]
    total: int


class DocumentChunkResponse(BaseModel):
    id: int
    document_id: int
    chunk_index: int
    content: str
    metadata: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentChunkListResponse(BaseModel):
    chunks: List[DocumentChunkResponse]
    total: int
