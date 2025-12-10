from typing import List

import PyPDF2
from langchain_text_splitters import RecursiveCharacterTextSplitter


class TextProcessor:
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    def read_file(self, path: str, filename: str) -> str:
        if filename.lower().endswith('.txt') or filename.lower().endswith('.md'):
            return self._read_text(path)
        elif filename.lower().endswith('.pdf'):
            return self._read_pdf(path)
        else:
            raise ValueError('Unsupported file type')


    def _read_text(self, path: str) -> str:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()


    def _read_pdf(self, path: str) -> str:
        text = ''
        with open(path, 'rb') as f:
            pdf = PyPDF2.PdfReader(f)
            for page in pdf.pages:
                page_text = page.extract_text() or ''
                text += page_text + '\n'
        return text


    def split(self, text: str) -> List[str]:
        return self.splitter.split_text(text)


text_processor = TextProcessor()