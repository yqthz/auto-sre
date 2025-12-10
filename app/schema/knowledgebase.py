from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

class KnowledgeBaseFile(BaseModel):
    file_id: int
    file_name: str
    file_url: str
    uploaded_at: datetime

class CreateKnowledgeBaseRequest(BaseModel):
    kb_name: str
    description: Optional[str]

class CreateKnowledgeBaseResponse(BaseModel):
    kb_id: int
    kb_name: str
    description: Optional[str]
    created_at: datetime

class UpdateKnowledgeBaseRequest(BaseModel):
    kb_id: int
    kb_name: str
    description: Optional[str]

class UpdateKnowledgeBaseResponse(BaseModel):
    kb_id: int
    kb_name: str
    description: Optional[str]
    updated_at: datetime

class DeleteKnowledgeBaseRequest(BaseModel):
    kb_id: int

class KnowledgeBaseListRequest(BaseModel):
    page: int
    size: int

class KnowledgeBaseListResponse(BaseModel):
    kb_id: int
    kb_name: str
    description: str
    create_at: datetime

class FileListResponse(BaseModel):
    file_id: int
    file_name: str



