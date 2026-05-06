from app.model.knowledge_base import KnowledgeBase
from app.model.user import User


def can_manage_knowledge_base(current_user: User, kb: KnowledgeBase) -> bool:
    return current_user.role == "admin" or kb.user_id == current_user.id


def kb_owner_name(owner: User | None) -> str | None:
    return owner.email if owner else None


def knowledge_base_response(kb: KnowledgeBase, owner: User | None = None) -> dict:
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "user_id": kb.user_id,
        "owner_name": kb_owner_name(owner),
        "created_at": kb.created_at,
        "updated_at": kb.updated_at,
        "document_count": kb.document_count,
        "chunk_count": kb.chunk_count,
    }


def rag_audit_details(current_user: User, kb: KnowledgeBase, **details) -> dict:
    return {
        "actor_user_id": current_user.id,
        "actor_user_role": current_user.role,
        "resource_owner_id": kb.user_id,
        **details,
    }
