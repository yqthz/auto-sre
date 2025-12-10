from typing import Tuple

from langchain_core.prompts import PromptTemplate

from app.core.logger import logger
from app.rag.pg_manager import pg_manager
from app.rag.text_processor import text_processor
from app.utils.file_utils import md5_of_file
from app.utils.llm_utils import get_llm, get_embeddings


class RAGSystem:
    def __init__(self, user_id: str, kb_name: str):
        self.user_id = user_id
        self.kb_name = kb_name
        self.llm = get_llm()
        self.embeddings = get_embeddings()
        self.prompt = PromptTemplate(
            input_variables=["context", "question"],
            template=(
                "You are a helpful assistant. Use the following context to answer the question.\n"
                "If you can't find the answer in the context, say you don't know.\n\n"
                "Context:\n{context}\n\nQuestion: {question}\nAnswer:"
            )
        )

    def add_documents(self, local_path: str, filename: str, file_url: str) -> Tuple[bool, str]:
        file_hash = md5_of_file(local_path)

        if pg_manager.check_file_exists(self.user_id, self.kb_name, file_hash):
            logger.info('Duplicate file detected')
            return True, 'File already indexed'

        # Read file
        try:
            content = text_processor.read_file(local_path, filename)
            chunks = text_processor.split(content)
        except Exception as e:
            logger.error(f'Read file failed: {e}')
            return False, f"Process failed: {str(e)}"

        if not chunks:
            return False, 'No content extracted from file'

        try:
            embeddings_list = self.embeddings.embed_documents(chunks)
        except Exception as e:
            return False, f"Embedding failed: {e}"

        file_info = {
            'user_id': self.user_id,
            'kb_name': self.kb_name,
            'file_name': filename,
            'file_hash': file_hash,
            'file_url': file_url
        }

        try:
            pg_manager.add_document_transaction(file_info, chunks, embeddings_list)
            return True, f'Added {len(chunks)} chunks to PG'
        except Exception as e:
            return False, f'Database write failed: {e}'



    def retrieve(self, query: str, top_k: int = 3) -> dict:
        query_embedding = self.embeddings.embed_query(query)

        docs = pg_manager.hybrid_search(
            query_embedding,
            query,
            self.user_id,
            self.kb_name,
            top_k
        )


        return {
            'query': query,
            'documents': docs
        }

    def query(self, question: str, top_k: int = 3) -> dict:
        retrieval_res = self.retrieve(question, top_k)
        docs =  retrieval_res['documents']

        if not docs:
            return {'question': question, 'answer': 'No relevant context found.'}

        context_text = "\n\n".join([d['content'] for d in docs])

        formatted_prompt = self.prompt.format(context=context_text, question=question)
        answer = self.llm.invoke(formatted_prompt)

        return {
            'question': question,
            'answer': answer,
            'source_documents': docs
        }
