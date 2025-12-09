from langchain_deepseek import ChatDeepSeek
from langchain_huggingface import HuggingFaceEmbeddings

from app.core.config import settings
from app.core.logger import logger

_global_embeddings = None
_global_llm = None

def get_embeddings():
    global _global_embeddings
    if _global_embeddings is None:
        logger.info('Loading embeddings model...')
        _global_embeddings = HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)
    return _global_embeddings


def get_llm():
    global _global_llm
    if _global_llm is None:
        logger.info('Initializing LLM client...')
        _global_llm = ChatDeepSeek(model=settings.LLM_MODEL, api_key=settings.LLM_API_KEY, streaming=True)
    return _global_llm
