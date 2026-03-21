"""
用户管理 API
仅管理员可以访问，用于管理系统用户
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, or_

from app.api import deps
from app.core import security
from app.model.user import User
from app.model.audit_log import AuditLog
from app.schema.user import UserCreate, UserUpdate, UserResponse, UserListResponse
from app.core.logger import logger

router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(deps.get_current_active_user)
):
    """
    获取当前登录用户的完整信息

    返回当前用户的所有信息，包括：
    - id: 用户ID
    - email: 邮箱
    - role: 角色（admin/sre/viewer）
    - is_active: 账号状态
    - created_at: 创建时间
    - updated_at: 更新时间
    - last_login_at: 最后登录时间
    """
    return UserResponse.model_validate(current_user)


@router.get("/users", response_model=UserListResponse)
async def get_users(
    skip: int = Query(0, ge=0, description="跳过的记录数"),
    limit: int = Query(50, ge=1, le=100, description="返回的记录数"),
    search: str = Query(None, description="搜索用户邮箱"),
    role: str = Query(None, description="按角色过滤"),
    is_active: bool = Query(None, description="按状态过滤"),
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    获取用户列表（仅管理员）

    支持：
    - 分页：skip 和 limit
    - 搜索：按邮箱搜索
    - 过滤：按角色和状态过滤
    """
    # 权限检查：仅管理员可以访问
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can access user management")

    # 构建查询
    query = select(User)

    # 搜索条件
    if search:
        query = query.where(User.email.ilike(f"%{search}%"))

    # 角色过滤
    if role:
        query = query.where(User.role == role)

    # 状态过滤
    if is_active is not None:
        query = query.where(User.is_active == is_active)

    # 排序：按创建时间倒序
    query = query.order_by(User.created_at.desc())

    # 统计总数
    count_query = select(func.count(User.id))
    if search:
        count_query = count_query.where(User.email.ilike(f"%{search}%"))
    if role:
        count_query = count_query.where(User.role == role)
    if is_active is not None:
        count_query = count_query.where(User.is_active == is_active)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # 分页查询
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()

    return UserListResponse(
        users=[UserResponse.model_validate(user) for user in users],
        total=total,
        skip=skip,
        limit=limit
    )


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    获取单个用户详情（仅管理员）
    """
    # 权限检查：仅管理员可以访问
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can access user management")

    # 查询用户
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse.model_validate(user)


@router.post("/users", response_model=UserResponse)
async def create_user(
    user_in: UserCreate,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    创建新用户（仅管理员）

    管理员可以创建任意角色的用户
    """
    # 权限检查：仅管理员可以创建用户
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can create users")

    # 验证角色
    if user_in.role not in ["admin", "sre", "viewer"]:
        raise HTTPException(status_code=400, detail="Invalid role. Must be admin, sre, or viewer")

    # 检查邮箱是否已存在
    result = await db.execute(select(User).where(User.email == user_in.email))
    if result.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system"
        )

    # 创建新用户
    user = User(
        email=user_in.email,
        hashed_password=security.get_password_hash(user_in.password),
        role=user_in.role,
        is_active=True
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # 记录审计日志
    audit = AuditLog(
        user_id=str(current_user.id),
        user_role=current_user.role,
        event_type="user_create",
        status="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={
            "created_user_id": user.id,
            "created_user_email": user.email,
            "created_user_role": user.role
        }
    )
    db.add(audit)
    await db.commit()

    logger.info(f"Admin {current_user.email} created user {user.email} with role {user.role}")

    return UserResponse.model_validate(user)


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    更新用户信息（仅管理员）

    可以更新：
    - 邮箱
    - 密码
    - 角色
    - 状态（激活/禁用）
    """
    # 权限检查：仅管理员可以更新用户
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can update users")

    # 查询用户
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 记录更新前的信息
    old_data = {
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active
    }

    # 更新邮箱
    if user_update.email is not None:
        # 检查新邮箱是否已被使用
        if user_update.email != user.email:
            email_check = await db.execute(
                select(User).where(User.email == user_update.email)
            )
            if email_check.scalars().first():
                raise HTTPException(
                    status_code=400,
                    detail="The user with this email already exists"
                )
        user.email = user_update.email

    # 更新密码
    if user_update.password is not None:
        user.hashed_password = security.get_password_hash(user_update.password)

    # 更新角色
    if user_update.role is not None:
        if user_update.role not in ["admin", "sre", "viewer"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid role. Must be admin, sre, or viewer"
            )
        user.role = user_update.role

    # 更新状态
    if user_update.is_active is not None:
        user.is_active = user_update.is_active

    # 更新时间戳
    user.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(user)

    # 记录审计日志
    audit = AuditLog(
        user_id=str(current_user.id),
        user_role=current_user.role,
        event_type="user_update",
        status="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={
            "updated_user_id": user.id,
            "old_data": old_data,
            "new_data": {
                "email": user.email,
                "role": user.role,
                "is_active": user.is_active
            }
        }
    )
    db.add(audit)
    await db.commit()

    logger.info(f"Admin {current_user.email} updated user {user.email}")

    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    删除用户（仅管理员）

    注意：
    - 不能删除自己
    - 删除操作会记录审计日志
    """
    # 权限检查：仅管理员可以删除用户
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can delete users")

    # 不能删除自己
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    # 查询用户
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 记录被删除用户的信息
    deleted_user_info = {
        "id": user.id,
        "email": user.email,
        "role": user.role
    }

    # 删除用户
    await db.delete(user)
    await db.commit()

    # 记录审计日志
    audit = AuditLog(
        user_id=str(current_user.id),
        user_role=current_user.role,
        event_type="user_delete",
        status="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={"deleted_user": deleted_user_info}
    )
    db.add(audit)
    await db.commit()

    logger.info(f"Admin {current_user.email} deleted user {deleted_user_info['email']}")

    return {
        "message": "User deleted successfully",
        "user_id": user_id
    }
