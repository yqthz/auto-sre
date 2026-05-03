"""
RAG 工具 - Agent 使用的知识库搜索工具
"""
import json
import re
from typing import Optional

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.agent.tools.security import register_tool
from app.rag.pg_manager import pg_manager
from app.storage import append_audit
from app.utils.format_utils import now_iso
from app.utils.llm_utils import get_embeddings


def _context_from_config(config: Optional[RunnableConfig]) -> tuple[str, str, str]:
    cfg = dict(config or {})
    configurable = dict(cfg.get("configurable") or {})
    user_id = str(configurable.get("user_id") or "unknown")
    user_role = str(configurable.get("user_role") or "unknown")
    thread_id = str(configurable.get("thread_id") or "global")
    return user_id, user_role, thread_id


def _append_rag_audit(
    *,
    tool_name: str,
    config: Optional[RunnableConfig],
    status: str,
    details: dict,
):
    user_id, user_role, thread_id = _context_from_config(config)
    append_audit(
        {
            "timestamp": now_iso(),
            "event": "rag_read",
            "tool": tool_name,
            "user_id": user_id,
            "user_role": user_role,
            "status": status,
            "details": {
                "thread_id": thread_id,
                "read_policy": "shared_read_owner_write",
                **details,
            },
        }
    )


@register_tool(
    name="search_knowledge_base",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["rag"]
)
async def search_knowledge_base(
    query: str,
    kb_id: Optional[int] = None,
    top_k: int = 5,
    config: Optional[RunnableConfig] = None,
) -> str:
    """
    【知识库搜索】使用混合检索（向量 + 关键词）召回并排序相关文档。
    当前 RAG 权限策略为“读共享、写隔离”：读取不按 user_id 隔离，
    写入、更新、删除等管理操作仍由 API 层按知识库 owner 校验。

    Args:
        query: 搜索关键词，如 "OOM killer"、"502 Bad Gateway"。
        kb_id: 可选；指定知识库范围。为空时在全部可读知识库中检索。
        top_k: 返回结果数量，范围会被限制到 1..20。
    Returns:
        JSON 字符串，格式为:
        {
          "ok": bool,
          "query": str,
          "kb_id": int | null,
          "top_k": int,
          "results": [
            {
              "rank": int,
              "document_id": int,
              "kb_id": int,
              "filename": str,
              "document_score": float,
              "matched_chunks": int,
              "best_chunk": {
                "chunk_id": int,
                "content": str,
                "metadata": dict
              }
            }
          ],
          "message": str,   # 仅空结果场景
          "error": str      # 仅失败场景
        }
    """
    logger.info(f"RAG tool: searching for '{query}' (kb_id={kb_id}, top_k={top_k})")
    top_k = max(1, min(int(top_k), 20))

    try:
        # 生成查询向量
        embeddings = get_embeddings()
        query_embedding = embeddings.embed_query(query)

        # 执行混合检索
        results = await pg_manager.hybrid_search_v2(
            query_embedding=query_embedding,
            query_text=query,
            kb_id=kb_id,
            top_k=top_k
        )

        if not results:
            _append_rag_audit(
                tool_name="search_knowledge_base",
                config=config,
                status="success",
                details={
                    "query": query,
                    "kb_id": kb_id,
                    "top_k": top_k,
                    "result_document_ids": [],
                    "result_count": 0,
                },
            )
            return json.dumps(
                {
                    "ok": True,
                    "query": query,
                    "kb_id": kb_id,
                    "top_k": top_k,
                    "results": [],
                    "message": "未找到相关文档"
                },
                ensure_ascii=False,
            )

        # 格式化文档级结果
        formatted_results = []
        for i, result in enumerate(results, 1):
            best_chunk = result.get("best_chunk") or {}
            chunk_content = best_chunk.get("content") or ""
            if len(chunk_content) > 500:
                chunk_content = chunk_content[:500] + "..."

            formatted_results.append(
                {
                    "rank": i,
                    "document_id": result.get("document_id"),
                    "kb_id": result.get("kb_id"),
                    "filename": result.get("filename"),
                    "document_score": round(float(result.get("document_score") or 0.0), 6),
                    "matched_chunks": int(result.get("matched_chunks") or 0),
                    "best_chunk": {
                        "chunk_id": best_chunk.get("chunk_id"),
                        "content": chunk_content,
                        "metadata": best_chunk.get("metadata") or {},
                    },
                }
            )

        _append_rag_audit(
            tool_name="search_knowledge_base",
            config=config,
            status="success",
            details={
                "query": query,
                "kb_id": kb_id,
                "top_k": top_k,
                "result_document_ids": [r.get("document_id") for r in results],
                "result_count": len(results),
            },
        )

        logger.info(f"RAG tool: found {len(results)} results")
        return json.dumps(
            {
                "ok": True,
                "query": query,
                "kb_id": kb_id,
                "top_k": top_k,
                "results": formatted_results,
            },
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"RAG tool search failed: {e}", exc_info=True)
        _append_rag_audit(
            tool_name="search_knowledge_base",
            config=config,
            status="failed",
            details={
                "query": query,
                "kb_id": kb_id,
                "top_k": top_k,
                "error": str(e),
            },
        )
        return json.dumps(
            {
                "ok": False,
                "query": query,
                "kb_id": kb_id,
                "top_k": top_k,
                "error": str(e),
            },
            ensure_ascii=False,
        )


@register_tool(
    name="list_knowledge_bases",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["rag"],
)
async def list_knowledge_bases(config: Optional[RunnableConfig] = None) -> str:
    """
    列出当前可检索的知识库，供 agent 获取 kb_id 后再调用 search_knowledge_base。
    当前 RAG 权限策略为“读共享、写隔离”：这里列出组织内 completed 文档所在知识库，
    不按 user_id 做读取隔离。

    Returns:
        JSON 字符串，格式为:
        {
          "ok": bool,
          "knowledge_bases": [
            {
              "kb_id": int,
              "name": str,
              "description": str,
              "document_count": int,
              "chunk_count": int
            }
          ]
        }
    """
    try:
        items = await pg_manager.list_knowledge_bases()
        _append_rag_audit(
            tool_name="list_knowledge_bases",
            config=config,
            status="success",
            details={
                "result_kb_ids": [item.get("kb_id") for item in items],
                "result_count": len(items),
            },
        )
        return json.dumps({"ok": True, "knowledge_bases": items}, ensure_ascii=False)
    except Exception as e:
        logger.error(f"RAG tool list knowledge bases failed: {e}", exc_info=True)
        _append_rag_audit(
            tool_name="list_knowledge_bases",
            config=config,
            status="failed",
            details={"error": str(e)},
        )
        return json.dumps({"ok": False, "error": str(e), "knowledge_bases": []}, ensure_ascii=False)


@register_tool(
    name="get_knowledge_document",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["rag"],
)
async def get_knowledge_document(document_id: int, config: Optional[RunnableConfig] = None) -> str:
    """
    获取知识库文档全文（只读 content_text，不做 chunk 拼接兜底）。
    当前 RAG 权限策略为“读共享、写隔离”：读取 completed 文档不按 user_id 隔离。

    Args:
        document_id: 文档 ID

    Returns:
        JSON 字符串。
    """
    try:
        record = await pg_manager.get_document_content(int(document_id))
        if not record:
            _append_rag_audit(
                tool_name="get_knowledge_document",
                config=config,
                status="failed",
                details={
                    "document_id": document_id,
                    "error": "DOCUMENT_NOT_FOUND",
                },
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "DOCUMENT_NOT_FOUND",
                    "document_id": document_id,
                },
                ensure_ascii=False,
            )

        if record.get("status") != "completed":
            _append_rag_audit(
                tool_name="get_knowledge_document",
                config=config,
                status="failed",
                details={
                    "document_id": record.get("document_id"),
                    "kb_id": record.get("kb_id"),
                    "document_status": record.get("status"),
                    "error": "DOCUMENT_NOT_READY",
                },
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "DOCUMENT_NOT_READY",
                    "document_id": record.get("document_id"),
                    "status": record.get("status"),
                },
                ensure_ascii=False,
            )

        content_text = record.get("content_text") or ""
        if not content_text.strip():
            _append_rag_audit(
                tool_name="get_knowledge_document",
                config=config,
                status="failed",
                details={
                    "document_id": record.get("document_id"),
                    "kb_id": record.get("kb_id"),
                    "document_status": record.get("status"),
                    "error": "DOCUMENT_CONTENT_NOT_READY",
                },
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "DOCUMENT_CONTENT_NOT_READY",
                    "document_id": record.get("document_id"),
                    "status": record.get("status"),
                },
                ensure_ascii=False,
            )

        _append_rag_audit(
            tool_name="get_knowledge_document",
            config=config,
            status="success",
            details={
                "document_id": record.get("document_id"),
                "kb_id": record.get("kb_id"),
                "filename": record.get("filename"),
                "content_length": len(content_text),
            },
        )
        return json.dumps(
            {
                "ok": True,
                "document_id": record.get("document_id"),
                "kb_id": record.get("kb_id"),
                "filename": record.get("filename"),
                "status": record.get("status"),
                "updated_at": record.get("updated_at"),
                "content_text": content_text,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error(f"RAG tool get knowledge document failed: {e}", exc_info=True)
        _append_rag_audit(
            tool_name="get_knowledge_document",
            config=config,
            status="failed",
            details={
                "document_id": document_id,
                "error": str(e),
            },
        )
        return json.dumps(
            {
                "ok": False,
                "error": str(e),
                "document_id": document_id,
            },
            ensure_ascii=False,
        )


@register_tool(
    name="get_knowledge_document_context",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["rag"],
)
async def get_knowledge_document_context(
    document_id: int,
    query: str,
    before_chars: int = 300,
    after_chars: int = 300,
    max_matches: int = 5,
    use_regex: bool = False,
    case_sensitive: bool = False,
    config: Optional[RunnableConfig] = None,
) -> str:
    """
    在文档全文(content_text)中定位 query，并回捞命中前后上下文。
    当前 RAG 权限策略为“读共享、写隔离”：读取 completed 文档不按 user_id 隔离。

    Args:
        document_id: 文档 ID
        query: 查询词或正则表达式
        before_chars: 命中前回捞字符数
        after_chars: 命中后回捞字符数
        max_matches: 最多返回命中数量
        use_regex: 是否将 query 按正则处理
        case_sensitive: 是否区分大小写

    Returns:
        JSON 字符串。
    """
    try:
        before_chars = max(50, min(int(before_chars), 2000))
        after_chars = max(50, min(int(after_chars), 2000))
        max_matches = max(1, min(int(max_matches), 20))

        record = await pg_manager.get_document_content(int(document_id))
        if not record:
            _append_rag_audit(
                tool_name="get_knowledge_document_context",
                config=config,
                status="failed",
                details={
                    "document_id": document_id,
                    "query": query,
                    "error": "DOCUMENT_NOT_FOUND",
                },
            )
            return json.dumps(
                {"ok": False, "error": "DOCUMENT_NOT_FOUND", "document_id": document_id},
                ensure_ascii=False,
            )

        if record.get("status") != "completed":
            _append_rag_audit(
                tool_name="get_knowledge_document_context",
                config=config,
                status="failed",
                details={
                    "document_id": record.get("document_id"),
                    "kb_id": record.get("kb_id"),
                    "document_status": record.get("status"),
                    "query": query,
                    "error": "DOCUMENT_NOT_READY",
                },
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "DOCUMENT_NOT_READY",
                    "document_id": record.get("document_id"),
                    "status": record.get("status"),
                },
                ensure_ascii=False,
            )

        content_text = record.get("content_text") or ""
        if not content_text.strip():
            _append_rag_audit(
                tool_name="get_knowledge_document_context",
                config=config,
                status="failed",
                details={
                    "document_id": record.get("document_id"),
                    "kb_id": record.get("kb_id"),
                    "document_status": record.get("status"),
                    "query": query,
                    "error": "DOCUMENT_CONTENT_NOT_READY",
                },
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "DOCUMENT_CONTENT_NOT_READY",
                    "document_id": record.get("document_id"),
                    "status": record.get("status"),
                },
                ensure_ascii=False,
            )

        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = query if use_regex else re.escape(query)
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            _append_rag_audit(
                tool_name="get_knowledge_document_context",
                config=config,
                status="failed",
                details={
                    "document_id": record.get("document_id"),
                    "kb_id": record.get("kb_id"),
                    "query": query,
                    "use_regex": use_regex,
                    "error": f"INVALID_REGEX: {e}",
                },
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": f"INVALID_REGEX: {e}",
                    "document_id": record.get("document_id"),
                },
                ensure_ascii=False,
            )

        matches = []
        for match in regex.finditer(content_text):
            start, end = match.span()
            left = max(0, start - before_chars)
            right = min(len(content_text), end + after_chars)
            snippet = content_text[left:right]

            matches.append(
                {
                    "match_text": match.group(0),
                    "start": start,
                    "end": end,
                    "context_start": left,
                    "context_end": right,
                    "context": snippet,
                }
            )
            if len(matches) >= max_matches:
                break

        _append_rag_audit(
            tool_name="get_knowledge_document_context",
            config=config,
            status="success",
            details={
                "document_id": record.get("document_id"),
                "kb_id": record.get("kb_id"),
                "filename": record.get("filename"),
                "query": query,
                "use_regex": use_regex,
                "case_sensitive": case_sensitive,
                "match_count": len(matches),
                "before_chars": before_chars,
                "after_chars": after_chars,
            },
        )
        return json.dumps(
            {
                "ok": True,
                "document_id": record.get("document_id"),
                "kb_id": record.get("kb_id"),
                "filename": record.get("filename"),
                "query": query,
                "use_regex": use_regex,
                "case_sensitive": case_sensitive,
                "before_chars": before_chars,
                "after_chars": after_chars,
                "total_matches": len(matches),
                "matches": matches,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error(f"RAG tool get document context failed: {e}", exc_info=True)
        _append_rag_audit(
            tool_name="get_knowledge_document_context",
            config=config,
            status="failed",
            details={
                "document_id": document_id,
                "query": query,
                "error": str(e),
            },
        )
        return json.dumps(
            {
                "ok": False,
                "error": str(e),
                "document_id": document_id,
                "query": query,
            },
            ensure_ascii=False,
        )
