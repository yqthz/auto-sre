from typing import Optional, Dict

from app.core.logger import logger
from app.rag.rag_system import RAGSystem
from app.tools.security import register_tool


@register_tool(
    name="search_knowledge_base",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["rag"]
)
def search_knowledge_base(query: str, filters: Optional[Dict] = None, top_k: int = 3) -> str:
    """
    【知识库搜索】当遇到未知的错误报错、不确定的排障步骤时，调用此工具。
    它会检索公司的运维手册(Runbooks)和历史故障案例。

    Args:
        query: 搜索关键词。请提炼报错的核心信息，例如 "OOM killer" 或 "502 Bad Gateway"。
        filters: (可选) 指定服务名称进行过滤，如 "payment-service"。
        top_k: 需要的文档数
    """
    logger.info(f"rag tool search {query}")

    rag_system = RAGSystem(user_id="current_user", kb_name="default")
    results = rag_system.retrieve(query, top_k=top_k, meta_filter=filters)
    docs = results['documents']
    if not docs:
        return "未找到相关文档。"

    context = "\n---\n".join([f"Content: {d['content']}\nMeta: {d['metadata']}" for d in docs])
    return context

