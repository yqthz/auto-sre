"""
RAG 工具 - Agent 使用的知识库搜索工具
"""
from typing import Optional
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.agent.tools.security import register_tool
from app.rag.pg_manager import pg_manager
from app.utils.llm_utils import get_embeddings


@register_tool(
    name="search_knowledge_base",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["rag"]
)
def search_knowledge_base(
    query: str,
    kb_id: Optional[int] = None,
    top_k: int = 5,
    config: Optional[RunnableConfig] = None
) -> str:
    """
    【知识库搜索】当遇到未知的错误报错、不确定的排障步骤时，调用此工具。
    它会检索公司的运维手册(Runbooks)和历史故障案例。

    使用混合检索（向量 + 关键词），返回最相关的文档片段。

    Args:
        query: 搜索关键词。请提炼报错的核心信息，例如 "OOM killer" 或 "502 Bad Gateway"。
        kb_id: (可选) 指定知识库 ID。如果不指定，则搜索用户的所有知识库。
        top_k: 返回的文档数量，默认 5 个
        config: LangChain 配置（自动传入，包含 user_id）

    Returns:
        格式化的搜索结果，包含文档内容和来源
    """
    logger.info(f"RAG tool: searching for '{query}' (kb_id={kb_id}, top_k={top_k})")

    # 从 config 中获取 user_id
    user_id = None
    if config and "configurable" in config:
        user_id_str = config["configurable"].get("user_id")
        if user_id_str:
            try:
                user_id = int(user_id_str)
            except ValueError:
                logger.error(f"Invalid user_id in config: {user_id_str}")

    if not user_id:
        logger.error("No user_id found in config")
        return "错误：无法获取用户信息，请重新登录。"

    try:
        # 生成查询向量
        embeddings = get_embeddings()
        query_embedding = embeddings.embed_query(query)

        # 执行混合检索
        results = pg_manager.hybrid_search_v2(
            query_embedding=query_embedding,
            query_text=query,
            user_id=user_id,
            kb_id=kb_id,
            top_k=top_k
        )

        if not results:
            return "未找到相关文档。建议：\n1. 尝试使用不同的关键词\n2. 检查知识库是否已上传相关文档"

        # 格式化结果
        formatted_results = []
        for i, result in enumerate(results, 1):
            content = result['content']
            filename = result['filename']
            score = result['score']

            # 限制每个片段的长度
            if len(content) > 500:
                content = content[:500] + "..."

            formatted_results.append(
                f"[文档 {i}] {filename} (相关度: {score:.3f})\n{content}"
            )

        context = "\n\n---\n\n".join(formatted_results)

        logger.info(f"RAG tool: found {len(results)} results")
        return context

    except Exception as e:
        logger.error(f"RAG tool search failed: {e}", exc_info=True)
        return f"搜索失败：{str(e)}"


