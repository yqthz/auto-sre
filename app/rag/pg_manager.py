import psycopg2
from psycopg2 import pool
import json
import jieba

from app.core.config import settings
from app.core.logger import logger


class PGManager:
    _pool = None

    def __init__(self, db_url=settings.DB_URL):
        if not PGManager._pool:
            PGManager._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1, maxconn=20, dsn=db_url
            )

    def _get_conn(self):
        return self._pool.getconn()

    def _put_conn(self, conn):
        self._pool.putconn(conn)

    def check_file_exists(self, user_id, kb_name, file_hash):
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM files WHERE user_id=%s AND kb_name=%s AND file_hash=%s",
                    (user_id, kb_name, file_hash)
                )
                return cur.fetchone() is not None
        finally:
            self._put_conn(conn)

    def add_document_transaction(self, file_info: dict, chunks: list, embeddings: list):
        """
        在一个事务中同时写入文件记录和向量切片
        """
        conn = self._get_conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    # 插入 documents 表
                    cur.execute(
                        """
                        INSERT INTO documents (user_id, kb_name, filename, file_hash, minio_url)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (file_info['user_id'], file_info['kb_name'],
                         file_info['filename'], file_info['file_hash'], file_info['file_url'])
                    )
                    file_id = cur.fetchone()[0]

                    # 批量准备 chunks 数据

                    data_values = []
                    for i, (text, vec) in enumerate(zip(chunks, embeddings)):
                        meta = {
                            'chunk_index': i,
                            'source': file_info['filename']
                        }
                        data_values.append((file_id, i, text, json.dumps(meta), vec))

                    # 批量插入 chunks
                    for row in data_values:
                        cur.execute(
                            """
                            INSERT INTO document_chunks (file_id, chunk_index, content, metadata, embedding)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            row
                        )
            return True
        except Exception as e:
            logger.error(f"DB Transaction failed: {e}")
            conn.rollback()
            raise e
        finally:
            self._put_conn(conn)

    def hybrid_search(self, query_embedding: list, query_text: str, user_id: str, kb_name: str, meta_filter: dict = None, top_k=3):
        """
        混合检索
        """
        conn = self._get_conn()

        # 对查询词进行中文分词，以便匹配 simple 配置的 tsvector
        # 这里简单处理，用 Jieba 切分并用 & 连接
        seg_list = jieba.cut(query_text)
        search_query = " & ".join(seg_list)

        filter_sql = ""
        filter_params = []
        if meta_filter:
            filter_sql = "AND dc.metadata @> %s::jsonb"
            filter_params = [json.dumps(meta_filter)]

        base_params = [query_embedding, user_id, kb_name]
        keyword_params = [search_query, user_id, kb_name, search_query]
        limit_param = [top_k]

        sql = f"""
        WITH semantic_search AS (
            SELECT dc.id, dc.content, dc.metadata,
                   1 - (dc.embedding <=> %s::vector) as similarity
            FROM document_chunks dc
            JOIN files f ON dc.file_id = f.id
            WHERE f.user_id = %s AND f.kb_name = %s
            {filter_sql}
            ORDER BY dc.embedding <=> %s::vector
            LIMIT 20
        ),
        keyword_search AS (
            SELECT dc.id, dc.content, dc.metadata,
                   ts_rank(dc.ts, to_tsquery('simple', %s)) as rank
            FROM document_chunks dc
            JOIN files f ON dc.file_id = f.id
            WHERE f.user_id = %s AND f.kb_name = %s
            {filter_sql} 
              AND dc.ts @@ to_tsquery('simple', %s)
            LIMIT 20
        )
        SELECT 
            COALESCE(s.id, k.id) as id,
            COALESCE(s.content, k.content) as content,
            COALESCE(s.metadata, k.metadata) as metadata,
            -- RRF 融合算法
            (COALESCE(1.0 / (60 + s.similarity * 100), 0.0) + 
             COALESCE(1.0 / (60 + k.rank), 0.0)) as score
        FROM semantic_search s
        FULL OUTER JOIN keyword_search k ON s.id = k.id
        ORDER BY score DESC
        LIMIT %s;
        """

        all_params = (
                base_params + filter_params + [query_embedding] +
                keyword_params + filter_params + limit_param
        )

        try:
            with conn.cursor() as cur:
                cur.execute(sql, all_params)
                results = cur.fetchall()
                return [
                    {'content': r[1], 'metadata': r[2], 'score': r[3]}
                    for r in results
                ]
        finally:
            self._put_conn(conn)

    def hybrid_search_v2(self, query_embedding: list, query_text: str, kb_id: int = None, top_k: int = 5):
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
        conn = self._get_conn()

        # 中文分词
        seg_list = jieba.cut(query_text)
        search_query = " & ".join(seg_list)

        # 构建 SQL
        kb_filter = ""
        params = {
            "query_embedding": query_embedding,
            "search_query": search_query,
            "top_k": top_k
        }

        if kb_id:
            kb_filter = "AND kb.id = %(kb_id)s"
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
                1 - (dc.embedding <=> %(query_embedding)s::vector) AS semantic_score,
                ROW_NUMBER() OVER (ORDER BY dc.embedding <=> %(query_embedding)s::vector) AS semantic_rank
            FROM rag_document_chunks dc
            JOIN rag_documents d ON dc.document_id = d.id
            JOIN knowledge_bases kb ON d.kb_id = kb.id
            WHERE d.status = 'completed'
              {kb_filter}
            ORDER BY dc.embedding <=> %(query_embedding)s::vector
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
                ts_rank(to_tsvector('simple', dc.content), to_tsquery('simple', %(search_query)s)) AS keyword_score,
                ROW_NUMBER() OVER (
                    ORDER BY ts_rank(to_tsvector('simple', dc.content), to_tsquery('simple', %(search_query)s)) DESC
                ) AS keyword_rank
            FROM rag_document_chunks dc
            JOIN rag_documents d ON dc.document_id = d.id
            JOIN knowledge_bases kb ON d.kb_id = kb.id
            WHERE d.status = 'completed'
              {kb_filter}
              AND to_tsvector('simple', dc.content) @@ to_tsquery('simple', %(search_query)s)
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
        LIMIT %(top_k)s
        """

        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                results = cur.fetchall()
                return [
                    {
                        'document_id': r[0],
                        'filename': r[1],
                        'kb_id': r[2],
                        'document_score': float(r[3]),
                        'matched_chunks': int(r[4] or 0),
                        'best_chunk': {
                            'chunk_id': r[5],
                            'content': r[6],
                            'metadata': json.loads(r[7]) if r[7] else {},
                        },
                    }
                    for r in results
                ]
        except Exception as e:
            logger.error(f"Hybrid search v2 failed: {e}", exc_info=True)
            return []
        finally:
            self._put_conn(conn)

    def list_knowledge_bases(self):
        """列出可检索的知识库（至少包含 1 个 completed 文档）。"""
        conn = self._get_conn()
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
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
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
        finally:
            self._put_conn(conn)

    def get_document_content(self, document_id: int):
        """按 document_id 获取文档全文（content_text）。"""
        conn = self._get_conn()
        sql = """
        SELECT
            d.id,
            d.kb_id,
            d.filename,
            d.status,
            d.content_text,
            d.updated_at
        FROM rag_documents d
        WHERE d.id = %s
        LIMIT 1
        """
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (document_id,))
                row = cur.fetchone()
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
        finally:
            self._put_conn(conn)


pg_manager = PGManager()
