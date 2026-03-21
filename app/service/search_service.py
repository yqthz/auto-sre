"""
RAG 向量搜索服务
使用 SQLAlchemy 实现混合检索（向量 + 关键词）
"""
import json
import jieba
from typing import List, Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select, func

from app.model.knowledge_base import KnowledgeBase, Document, DocumentChunk
from app.utils.llm_utils import get_embeddings
from app.core.logger import logger


class SearchService:
    """向量搜索服务"""

    def __init__(self):
        self.embeddings = get_embeddings()

    async def vector_search(
        self,
        db: AsyncSession,
        query: str,
        user_id: int,
        kb_id: Optional[int] = None,
        top_k: int = 5
    ) -> List[Dict]:
        """
        纯向量搜索

        Args:
            db: 数据库会话
            query: 搜索查询
            user_id: 用户 ID
            kb_id: 知识库 ID（可选，不指定则搜索所有知识库）
            top_k: 返回结果数量

        Returns:
            搜索结果列表
        """
        try:
            # 生成查询向量
            query_embedding = self.embeddings.embed_query(query)

            # 构建 SQL 查询
            sql = """
            SELECT
                dc.id,
                dc.document_id,
                dc.content,
                dc.metadata,
                d.filename,
                d.kb_id,
                1 - (dc.embedding <=> :query_embedding::vector) as similarity
            FROM rag_document_chunks dc
            JOIN rag_documents d ON dc.document_id = d.id
            JOIN knowledge_bases kb ON d.kb_id = kb.id
            WHERE kb.user_id = :user_id
            """

            params = {
                "query_embedding": query_embedding,
                "user_id": user_id,
                "top_k": top_k
            }

            # 如果指定了知识库，添加过滤条件
            if kb_id:
                sql += " AND kb.id = :kb_id"
                params["kb_id"] = kb_id

            # 只搜索已完成处理的文档
            sql += " AND d.status = 'completed'"

            # 排序和限制
            sql += """
            ORDER BY dc.embedding <=> :query_embedding::vector
            LIMIT :top_k
            """

            result = await db.execute(text(sql), params)
            rows = result.fetchall()

            return [
                {
                    "chunk_id": row[0],
                    "document_id": row[1],
                    "content": row[2],
                    "metadata": json.loads(row[3]) if row[3] else {},
                    "filename": row[4],
                    "kb_id": row[5],
                    "score": float(row[6])
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(f"Vector search failed: {e}", exc_info=True)
            return []

    async def keyword_search(
        self,
        db: AsyncSession,
        query: str,
        user_id: int,
        kb_id: Optional[int] = None,
        top_k: int = 20
    ) -> List[Dict]:
        """
        关键词搜索（使用 PostgreSQL 全文搜索）

        Args:
            db: 数据库会话
            query: 搜索查询
            user_id: 用户 ID
            kb_id: 知识库 ID（可选）
            top_k: 返回结果数量

        Returns:
            搜索结果列表
        """
        try:
            # 使用 jieba 分词
            seg_list = jieba.cut(query)
            search_query = " & ".join(seg_list)

            # 构建 SQL 查询
            sql = """
            SELECT
                dc.id,
                dc.document_id,
                dc.content,
                dc.metadata,
                d.filename,
                d.kb_id,
                ts_rank(to_tsvector('simple', dc.content), to_tsquery('simple', :search_query)) as rank
            FROM rag_document_chunks dc
            JOIN rag_documents d ON dc.document_id = d.id
            JOIN knowledge_bases kb ON d.kb_id = kb.id
            WHERE kb.user_id = :user_id
              AND d.status = 'completed'
              AND to_tsvector('simple', dc.content) @@ to_tsquery('simple', :search_query)
            """

            params = {
                "search_query": search_query,
                "user_id": user_id,
                "top_k": top_k
            }

            if kb_id:
                sql += " AND kb.id = :kb_id"
                params["kb_id"] = kb_id

            sql += """
            ORDER BY rank DESC
            LIMIT :top_k
            """

            result = await db.execute(text(sql), params)
            rows = result.fetchall()

            return [
                {
                    "chunk_id": row[0],
                    "document_id": row[1],
                    "content": row[2],
                    "metadata": json.loads(row[3]) if row[3] else {},
                    "filename": row[4],
                    "kb_id": row[5],
                    "rank": float(row[6])
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(f"Keyword search failed: {e}", exc_info=True)
            return []

    async def hybrid_search(
        self,
        db: AsyncSession,
        query: str,
        user_id: int,
        kb_id: Optional[int] = None,
        top_k: int = 5
    ) -> List[Dict]:
        """
        混合检索（向量 + 关键词，使用 RRF 融合）

        Args:
            db: 数据库会话
            query: 搜索查询
            user_id: 用户 ID
            kb_id: 知识库 ID（可选）
            top_k: 返回结果数量

        Returns:
            搜索结果列表
        """
        try:
            # 生成查询向量
            query_embedding = self.embeddings.embed_query(query)

            # 使用 jieba 分词
            seg_list = jieba.cut(query)
            search_query = " & ".join(seg_list)

            # 构建混合搜索 SQL（使用 RRF 融合算法）
            sql = """
            WITH semantic_search AS (
                SELECT
                    dc.id,
                    dc.document_id,
                    dc.content,
                    dc.metadata,
                    d.filename,
                    d.kb_id,
                    1 - (dc.embedding <=> :query_embedding::vector) as similarity
                FROM rag_document_chunks dc
                JOIN rag_documents d ON dc.document_id = d.id
                JOIN knowledge_bases kb ON d.kb_id = kb.id
                WHERE kb.user_id = :user_id
                  AND d.status = 'completed'
            """

            params = {
                "query_embedding": query_embedding,
                "search_query": search_query,
                "user_id": user_id,
                "top_k": top_k
            }

            if kb_id:
                sql += " AND kb.id = :kb_id"
                params["kb_id"] = kb_id

            sql += """
                ORDER BY dc.embedding <=> :query_embedding::vector
                LIMIT 20
            ),
            keyword_search AS (
                SELECT
                    dc.id,
                    dc.document_id,
                    dc.content,
                    dc.metadata,
                    d.filename,
                    d.kb_id,
                    ts_rank(to_tsvector('simple', dc.content), to_tsquery('simple', :search_query)) as rank
                FROM rag_document_chunks dc
                JOIN rag_documents d ON dc.document_id = d.id
                JOIN knowledge_bases kb ON d.kb_id = kb.id
                WHERE kb.user_id = :user_id
                  AND d.status = 'completed'
                  AND to_tsvector('simple', dc.content) @@ to_tsquery('simple', :search_query)
            """

            if kb_id:
                sql += " AND kb.id = :kb_id"

            sql += """
                LIMIT 20
            )
            SELECT
                COALESCE(s.id, k.id) as chunk_id,
                COALESCE(s.document_id, k.document_id) as document_id,
                COALESCE(s.content, k.content) as content,
                COALESCE(s.metadata, k.metadata) as metadata,
                COALESCE(s.filename, k.filename) as filename,
                COALESCE(s.kb_id, k.kb_id) as kb_id,
                -- RRF 融合算法 (Reciprocal Rank Fusion)
                (COALESCE(1.0 / (60 + s.similarity * 100), 0.0) +
                 COALESCE(1.0 / (60 + k.rank * 100), 0.0)) as score
            FROM semantic_search s
            FULL OUTER JOIN keyword_search k ON s.id = k.id
            ORDER BY score DESC
            LIMIT :top_k
            """

            result = await db.execute(text(sql), params)
            rows = result.fetchall()

            return [
                {
                    "chunk_id": row[0],
                    "document_id": row[1],
                    "content": row[2],
                    "metadata": json.loads(row[3]) if row[3] else {},
                    "filename": row[4],
                    "kb_id": row[5],
                    "score": float(row[6])
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(f"Hybrid search failed: {e}", exc_info=True)
            return []

    async def search_with_context(
        self,
        db: AsyncSession,
        query: str,
        user_id: int,
        kb_id: Optional[int] = None,
        top_k: int = 5,
        use_hybrid: bool = True
    ) -> Dict:
        """
        搜索并返回格式化的上下文

        Args:
            db: 数据库会话
            query: 搜索查询
            user_id: 用户 ID
            kb_id: 知识库 ID（可选）
            top_k: 返回结果数量
            use_hybrid: 是否使用混合检索

        Returns:
            包含查询和结果的字典
        """
        if use_hybrid:
            results = await self.hybrid_search(db, query, user_id, kb_id, top_k)
        else:
            results = await self.vector_search(db, query, user_id, kb_id, top_k)

        return {
            "query": query,
            "results": results,
            "total": len(results)
        }

    def format_context_for_llm(self, search_results: List[Dict]) -> str:
        """
        将搜索结果格式化为 LLM 上下文

        Args:
            search_results: 搜索结果列表

        Returns:
            格式化的上下文字符串
        """
        if not search_results:
            return "未找到相关文档。"

        context_parts = []
        for i, result in enumerate(search_results, 1):
            context_parts.append(
                f"[文档 {i}] {result['filename']}\n"
                f"{result['content']}\n"
                f"(相关度: {result['score']:.3f})"
            )

        return "\n\n---\n\n".join(context_parts)


# 全局单例
search_service = SearchService()
