"""鉴权核心：密码哈希、JWT 签发、当前用户依赖。

路由层只需 `current_user: str = Depends(get_current_user)`，
不必在每个端点重复解析 token / 查库。
"""

from datetime import timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import select

from app.core.config import get_settings
from app.core.dates import now
from app.models.db import session_scope
from app.models.tables import User

# tokenUrl 仅用于 OpenAPI 文档的「Authorize」按钮；登录端点见 router
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def hash_password(plain: str) -> str:
    """bcrypt 加盐哈希（内含随机 salt）。"""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    """校验明文与哈希是否匹配；哈希格式非法时返回 False，不抛异常。"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(username: str) -> str:
    """签发带 sub/exp 的 JWT。"""
    settings = get_settings()
    expire = now() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

def _auth_error(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )

def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    """解析 JWT 并确认用户仍存在，返回 username。

    - 缺失/伪造 token、用户已删除 → 401
    - token 过期 → 401（明确提示 expired）
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        username = payload.get("sub")
    except jwt.ExpiredSignatureError:
        raise _auth_error("token expired") from None
    except jwt.PyJWTError:
        raise _auth_error("could not validate credentials") from None
    if not isinstance(username, str) or not username:
        raise _auth_error("could not validate credentials")
    with session_scope() as s:
        user = s.exec(select(User).where(User.username == username)).first()
        if not user:
            raise _auth_error("user no longer exists")
    return username

