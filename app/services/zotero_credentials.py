"""用户自己的 Zotero 凭证；数据库仅保存 Fernet 密文。"""

from __future__ import annotations

from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core.config import get_settings
from app.models.db import session_scope
from app.models.tables import Integration, _now

PROVIDER = "zotero"


class CredentialConfigurationError(RuntimeError):
    """加密密钥缺失/无效，或已保存的凭证无法用当前密钥解密。"""


@dataclass(frozen=True)
class ZoteroCredential:
    zotero_user_id: str
    api_key: str = field(repr=False)


def _fernet() -> Fernet:
    key = get_settings().INTEGRATION_SECRET.strip()
    if not key:
        raise CredentialConfigurationError("INTEGRATION_SECRET is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise CredentialConfigurationError("INTEGRATION_SECRET is not a Fernet key") from exc


def ensure_storage_ready() -> None:
    """绑定前检查加密配置，避免验证完用户 Key 才发现无法安全保存。"""
    _fernet()


def load_credential(user_id: str) -> ZoteroCredential | None:
    """只读取当前 LabPilot 用户的 Zotero 凭证；解密失败时拒绝使用。"""
    with session_scope() as session:
        row = session.exec(
            select(Integration).where(
                Integration.user_id == user_id, Integration.provider == PROVIDER
            )
        ).first()
        if row is None:
            return None
        zotero_user_id, encrypted = row.zotero_user_id, row.api_key_enc
    try:
        api_key = _fernet().decrypt(encrypted.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError) as exc:
        raise CredentialConfigurationError("Saved Zotero credential cannot be decrypted") from exc
    return ZoteroCredential(zotero_user_id=zotero_user_id, api_key=api_key)


def has_credential(user_id: str) -> bool:
    """Agent 的工具可见性检查。加密配置异常时关闭工具，避免跨用户回退。"""
    try:
        return load_credential(user_id) is not None
    except CredentialConfigurationError:
        return False


def save_credential(user_id: str, zotero_user_id: str, api_key: str) -> None:
    """校验通过后绑定/更新；调用方必须先经 MCP 验证 key。"""
    if not user_id or not zotero_user_id or not api_key:
        raise ValueError("user_id, zotero_user_id and api_key are required")
    encrypted = _fernet().encrypt(api_key.encode("utf-8")).decode("ascii")
    for attempt in range(2):
        try:
            with session_scope() as session:
                row = session.exec(
                    select(Integration).where(
                        Integration.user_id == user_id, Integration.provider == PROVIDER
                    )
                ).first()
                if row is None:
                    session.add(
                        Integration(
                            user_id=user_id,
                            provider=PROVIDER,
                            zotero_user_id=zotero_user_id,
                            api_key_enc=encrypted,
                        )
                    )
                else:
                    row.zotero_user_id = zotero_user_id
                    row.api_key_enc = encrypted
                    row.updated_at = _now()
                    session.add(row)
            return
        except IntegrityError:
            # 两次并发首次绑定时唯一索引只允许一条；输家重查后改为更新。
            if attempt:
                raise


def delete_credential(user_id: str) -> bool:
    with session_scope() as session:
        row = session.exec(
            select(Integration).where(
                Integration.user_id == user_id, Integration.provider == PROVIDER
            )
        ).first()
        if row is None:
            return False
        session.delete(row)
        return True
