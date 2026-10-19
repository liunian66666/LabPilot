"""稳定拟信标识；显式新一轮发送使用独立标识，不复用旧批准。"""

import hashlib
import json


def intent_key(user_id: str, session_id: str, version: str) -> str:
    return hashlib.sha256(
        json.dumps([user_id, session_id, version], ensure_ascii=False).encode()
    ).hexdigest()
