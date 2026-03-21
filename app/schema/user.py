from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field

# 基础模型
class UserBase(BaseModel):
    email: EmailStr

# 创建用户时需要密码
class UserCreate(UserBase):
    password: str
    role: Optional[str] = "viewer"  # 默认为 viewer 角色

# 更新用户（管理员使用）
class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None

# 返回给前端时不包含密码
class UserResponse(UserBase):
    id: int
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# 用户列表响应
class UserListResponse(BaseModel):
    users: List[UserResponse]
    total: int
    skip: int
    limit: int

# Token 响应结构
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"