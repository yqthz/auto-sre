"""
RAG 知识库数据库模型
使用 SQLAlchemy ORM + pgvector
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from app.model.user import Base


class KnowledgeBase(Base):
    """知识库表"""
    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True, index=True)

    # 知识库名称
    name = Column(String(255), nullable=False)

    # 知识库描述
    description = Column(Text, nullable=True)

    # 所属用户
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # 是否公开（未来扩展：团队共享）
    is_public = Column(Boolean, default=False, nullable=False)

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # 统计信息（冗余字段，提高查询性能）
    document_count = Column(Integer, default=0, nullable=False)
    chunk_count = Column(Integer, default=0, nullable=False)

    # 关联文档
    documents = relationship("Document", back_populates="knowledge_base", cascade="all, delete-orphan")


class Document(Base):
    """文档表"""
    __tablename__ = "rag_documents"

    id = Column(Integer, primary_key=True, index=True)

    # 所属知识库
    kb_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=False, index=True)

    # 文件名
    filename = Column(String(255), nullable=False)

    # 文件哈希（用于去重）
    file_hash = Column(String(64), nullable=False, index=True)

    # MinIO 存储路径
    minio_url = Column(String(512), nullable=True)

    # 文件大小（字节）
    file_size = Column(Integer, nullable=True)

    # 文件类型（pdf, txt, md, docx 等）
    file_type = Column(String(50), nullable=True)

    # 处理状态：pending（待处理）、processing（处理中）、completed（完成）、failed（失败）
    status = Column(String(50), default="pending", nullable=False, index=True)

    # 错误信息（如果处理失败）
    error_message = Column(Text, nullable=True)

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # 统计信息
    chunk_count = Column(Integer, default=0, nullable=False)

    # 关联知识库
    knowledge_base = relationship("KnowledgeBase", back_populates="documents")

    # 关联分块
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base):
    """文档分块表（包含向量）"""
    __tablename__ = "rag_document_chunks"

    id = Column(Integer, primary_key=True, index=True)

    # 所属文档
    document_id = Column(Integer, ForeignKey("rag_documents.id"), nullable=False, index=True)

    # 分块索引（在文档中的位置）
    chunk_index = Column(Integer, nullable=False)

    # 分块内容
    content = Column(Text, nullable=False)

    # 向量嵌入（使用 pgvector）
    # 维度根据 embedding 模型决定
    embedding = Column(Vector(1024), nullable=True)

    # 元数据（JSON 格式，存储额外信息）
    # 例如：{"page": 1, "section": "Introduction"}
    metadata_ = Column("metadata", Text, nullable=True)

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # 关联文档
    document = relationship("Document", back_populates="chunks")
