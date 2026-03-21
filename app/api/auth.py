from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core import security
from app.model.user import User
from app.model.audit_log import AuditLog
from app.schema.user import UserCreate, UserResponse, Token

router = APIRouter()

@router.post("/signup", response_model=UserResponse)
async def create_user(
    user_in: UserCreate,
    request: Request,
    db: AsyncSession = Depends(deps.get_db)
):
    # 检查邮箱是否已存在
    result = await db.execute(select(User).where(User.email == user_in.email))
    if result.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system.",
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
        user_id=str(user.id),
        user_role=user.role,
        event_type="user_signup",
        status="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={"email": user.email}
    )
    db.add(audit)
    await db.commit()

    return user


@router.post("/login", response_model=Token)
async def login_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(deps.get_db)
):
    # 查找用户
    # 注意：OAuth2PasswordRequestForm 将 email 字段放在 username 属性中
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalars().first()

    # 验证用户和密码
    if not user or not security.verify_password(form_data.password, user.hashed_password):
        # 记录失败的登录尝试
        if user:
            audit = AuditLog(
                user_id=str(user.id),
                user_role=user.role,
                event_type="login",
                status="failed",
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                error_message="Incorrect password"
            )
            db.add(audit)
            await db.commit()

        raise HTTPException(status_code=400, detail="Incorrect email or password")

    if not user.is_active:
        # 记录被禁用用户的登录尝试
        audit = AuditLog(
            user_id=str(user.id),
            user_role=user.role,
            event_type="login",
            status="denied",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            error_message="User account is inactive"
        )
        db.add(audit)
        await db.commit()

        raise HTTPException(status_code=400, detail="Inactive user")

    # 更新最后登录时间
    user.last_login_at = datetime.utcnow()
    await db.commit()

    # 记录成功的登录
    audit = AuditLog(
        user_id=str(user.id),
        user_role=user.role,
        event_type="login",
        status="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent")
    )
    db.add(audit)
    await db.commit()

    # 生成 Token
    return {
        "access_token": security.create_access_token(user.id),
        "token_type": "bearer"
    }