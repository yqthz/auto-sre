import json
from typing import Any

import jieba
from sqlalchemy import text

from app.core.logger import logger
from app.db.session import AsyncSessionLocal


def _vector_literal(values: list[float]) -> str:
    """Return pgvector text literal for a bound parameter."""
    return "[" + ",".join(str(float(v)) for v in values) + "]"


def _json_object(value: Any) -> dict:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class PGManager:
    async def hybrid_search_v2(
        self,
        query_embedding: list[float],
        query_text: str,
        kb_id: int | None = None,
        top_k: int = 5,
    ):
        """
        混合检索 V2 - 使用新的表结构（knowledge_bases, rag_documents, rag_document_chunks）

        Args:
            query_embedding: 查询向量
            query_text: 查询文本
            kb_id: 知识库 ID（可选，不指定则搜索所有知识库）
            top_k: 返回结果数量

        Returns:
            文档级搜索结果列表，包含 document_score 和 best_chunk 信息
        """
        seg_list = jieba.cut(query_text)
        search_query = " & ".join(seg_list)

        kb_filter = ""
        params = {
            "query_embedding": _vector_literal(query_embedding),
            "search_query": search_query,
            "top_k": top_k,
        }

        if kb_id:
            kb_filter = "AND kb.id = :kb_id"
            params["kb_id"] = kb_id

        sql = f"""
        WITH semantic_candidates AS (
            SELECT
                dc.id AS chunk_id,
                dc.document_id,
                dc.content,
                dc.metadata,
                d.filename,
                d.kb_id,
                1 - (dc.embedding <=> CAST(:query_embedding AS vector)) AS semantic_score,
                ROW_NUMBER() OVER (ORDER BY dc.embedding <=> CAST(:query_embedding AS vector)) AS semantic_rank
            FROM rag_document_chunks dc
            JOIN rag_documents d ON dc.document_id = d.id
            JOIN knowledge_bases kb ON d.kb_id = kb.id
            WHERE d.status = 'completed'
              {kb_filter}
            ORDER BY dc.embedding <=> CAST(:query_embedding AS vector)
            LIMIT 100
        ),
        keyword_candidates AS (
            SELECT
                dc.id AS chunk_id,
                dc.document_id,
                dc.content,
                dc.metadata,
                d.filename,
                d.kb_id,
                ts_rank(to_tsvector('simple', dc.content), to_tsquery('simple', :search_query)) AS keyword_score,
                ROW_NUMBER() OVER (
                    ORDER BY ts_rank(to_tsvector('simple', dc.content), to_tsquery('simple', :search_query)) DESC
                ) AS keyword_rank
            FROM rag_document_chunks dc
            JOIN rag_documents d ON dc.document_id = d.id
            JOIN knowledge_bases kb ON d.kb_id = kb.id
            WHERE d.status = 'completed'
              {kb_filter}
              AND to_tsvector('simple', dc.content) @@ to_tsquery('simple', :search_query)
            ORDER BY keyword_score DESC
            LIMIT 100
        ),
        chunk_union AS (
            SELECT
                COALESCE(s.chunk_id, k.chunk_id) AS chunk_id,
                COALESCE(s.document_id, k.document_id) AS document_id,
                COALESCE(s.content, k.content) AS content,
                COALESCE(s.metadata, k.metadata) AS metadata,
                COALESCE(s.filename, k.filename) AS filename,
                COALESCE(s.kb_id, k.kb_id) AS kb_id,
                COALESCE(s.semantic_score, 0.0) AS semantic_score,
                COALESCE(k.keyword_score, 0.0) AS keyword_score,
                COALESCE(1.0 / (60 + s.semantic_rank), 0.0) AS semantic_rrf,
                COALESCE(1.0 / (60 + k.keyword_rank), 0.0) AS keyword_rrf
            FROM semantic_candidates s
            FULL OUTER JOIN keyword_candidates k ON s.chunk_id = k.chunk_id
        ),
        chunk_scored AS (
            SELECT
                *,
                (semantic_rrf + keyword_rrf) AS chunk_score,
                ROW_NUMBER() OVER (
                    PARTITION BY document_id
                    ORDER BY (semantic_rrf + keyword_rrf) DESC, chunk_id ASC
                ) AS doc_chunk_rank
            FROM chunk_union
        ),
        document_scored AS (
            SELECT
                document_id,
                MIN(filename) AS filename,
                MIN(kb_id) AS kb_id,
                MAX(chunk_score) AS best_chunk_score,
                AVG(chunk_score) FILTER (WHERE doc_chunk_rank <= 3) AS top3_avg_chunk_score,
                COUNT(*) AS matched_chunks,
                MAX(CASE WHEN doc_chunk_rank = 1 THEN chunk_id END) AS best_chunk_id,
                MAX(CASE WHEN doc_chunk_rank = 1 THEN content END) AS best_chunk_content,
                MAX(CASE WHEN doc_chunk_rank = 1 THEN metadata END) AS best_chunk_metadata
            FROM chunk_scored
            GROUP BY document_id
        )
        SELECT
            document_id,
            filename,
            kb_id,
            (
                COALESCE(best_chunk_score, 0.0) * 0.6 +
                COALESCE(top3_avg_chunk_score, 0.0) * 0.3 +
                LEAST(matched_chunks, 5) * 0.02
            ) AS document_score,
            matched_chunks,
            best_chunk_id,
            best_chunk_content,
            best_chunk_metadata
        FROM document_scored
        ORDER BY document_score DESC
        LIMIT :top_k
        """

        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(text(sql), params)
                rows = result.fetchall()
            return [
                {
                    "document_id": r[0],
                    "filename": r[1],
                    "kb_id": r[2],
                    "document_score": float(r[3]),
                    "matched_chunks": int(r[4] or 0),
                    "best_chunk": {
                        "chunk_id": r[5],
                        "content": r[6],
                        "metadata": _json_object(r[7]),
                    },
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Hybrid search v2 failed: {e}", exc_info=True)
            return []

    async def list_knowledge_bases(self):
        """列出可检索的知识库（至少包含 1 个 completed 文档）。"""
        sql = """
        SELECT
            kb.id,
            kb.name,
            kb.description,
            kb.document_count,
            kb.chunk_count
        FROM knowledge_bases kb
        WHERE EXISTS (
            SELECT 1
            FROM rag_documents d
            WHERE d.kb_id = kb.id
              AND d.status = 'completed'
        )
        ORDER BY kb.id ASC
        """
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(text(sql))
                rows = result.fetchall()
            return [
                {
                    "kb_id": r[0],
                    "name": r[1],
                    "description": r[2] or "",
                    "document_count": int(r[3] or 0),
                    "chunk_count": int(r[4] or 0),
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"List knowledge bases failed: {e}", exc_info=True)
            return []

    async def get_document_content(self, document_id: int):
        """按 document_id 获取文档全文（content_text）。"""
        sql = """
        SELECT
            d.id,
            d.kb_id,
            d.filename,
            d.status,
            d.content_text,
            d.updated_at
        FROM rag_documents d
        WHERE d.id = :document_id
        LIMIT 1
        """
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(text(sql), {"document_id": document_id})
                row = result.fetchone()
            if not row:
                return None
            return {
                "document_id": row[0],
                "kb_id": row[1],
                "filename": row[2],
                "status": row[3],
                "content_text": row[4],
                "updated_at": row[5].isoformat() if row[5] else None,
            }
        except Exception as e:
            logger.error(f"Get document content failed: {e}", exc_info=True)
            return None


pg_manager = PGManager()
