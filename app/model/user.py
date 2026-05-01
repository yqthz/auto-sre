from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)

    # 角色：admin（管理员）、sre（运维）、viewer（只读）
    role = Column(String, default="viewer", nullable=False)

    # 账号状态
    is_active = Column(Boolean, default=True, nullable=False)

    # 审计字段：记录创建和更新时间
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # 最后登录时间
    last_login_at = Column(DateTime, nullable=True)