"""按登录用户绑定 Zotero，并经 MCP 执行只读检索。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, SecretStr

from app.core.security import get_current_user
from app.services.audit import audit
from app.services.zotero_credentials import (
    CredentialConfigurationError,
    delete_credential,
    ensure_storage_ready,
    load_credential,
    save_credential,
)

router = APIRouter(prefix="/zotero", tags=["zotero"])


class ZoteroBindRequest(BaseModel):
    api_key: SecretStr = Field(min_length=1)


@router.get("/credential")
def get_zotero_credential(current_user: str = Depends(get_current_user)) -> dict:
    """只返回绑定状态与 Zotero 用户 ID，永不回传 API Key 或密文。"""
    try:
        cred = load_credential(current_user)
    except CredentialConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Zotero 凭证服务配置异常") from exc
    if cred is None:
        return {"connected": False}
    return {"connected": True, "zotero_user_id": cred.zotero_user_id}


@router.post("/credential")
@router.put("/credential")
async def bind_zotero_credential(
    body: ZoteroBindRequest,
    current_user: str = Depends(get_current_user),
) -> dict:
    """先通过 MCP 验证 Key，再加密覆盖当前用户的旧绑定。"""
    from app.services.zotero_mcp import verify_zotero_credentials

    api_key = body.api_key.get_secret_value().strip()
    if not api_key:
        raise HTTPException(status_code=422, detail="Zotero API Key 不能为空")
    try:
        ensure_storage_ready()
    except CredentialConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Zotero 凭证服务配置异常") from exc
    try:
        verified = await verify_zotero_credentials(api_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Zotero API Key 无效或无读取权限") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Zotero MCP 暂时不可用") from exc
    zotero_user_id = str(verified.get("user_id") or "").strip()
    if not zotero_user_id:
        raise HTTPException(status_code=503, detail="Zotero MCP 未返回用户 ID")
    try:
        save_credential(current_user, zotero_user_id, api_key)
    except CredentialConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Zotero 凭证服务配置异常") from exc
    audit(current_user, "zotero_connected", zotero_user_id=zotero_user_id)
    return {"connected": True, "zotero_user_id": zotero_user_id}


@router.delete("/credential")
def unbind_zotero_credential(current_user: str = Depends(get_current_user)) -> dict:
    deleted = delete_credential(current_user)
    if deleted:
        audit(current_user, "zotero_disconnected")
    return {"connected": False}


@router.get("/search")
async def search_zotero(
    q: str = Query(min_length=1, max_length=200),
    current_user: str = Depends(get_current_user),
) -> dict:
    """前端检索与 Agent 共用按用户路由的 MCP 客户端。"""
    from app.services.zotero_mcp import search_reference_items

    try:
        if load_credential(current_user) is None:
            raise HTTPException(status_code=409, detail="请先绑定 Zotero")
        items = await search_reference_items(q.strip(), current_user)
    except CredentialConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Zotero 凭证服务配置异常") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Zotero 检索词无效") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Zotero MCP 暂时不可用") from exc
    return {"items": items}
