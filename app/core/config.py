"""应用配置：从环境变量读取，禁止硬编码密钥。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(BASE_DIR / ".env"), str(BASE_DIR / ".env.local")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "LabPilot"
    APP_ENV: str = "development"
    API_V1_PREFIX: str = "/api/v1"
    TIMEZONE: str = "Asia/Shanghai"
    WEEK_START: int = 1  # ISO 周一

    # 鉴权（JWT）：生产必须通过环境变量覆盖 JWT_SECRET_KEY；留空仅用于本地/测试
    JWT_SECRET_KEY: str = "dev-insecure-secret-change-me-in-labpilot-0123456789"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 720  # 12 小时

    # 存储：开发默认 SQLite；生产用 Postgres（可带 pgvector）
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'labpilot.db'}"
    # LangGraph 检查点（会话记忆 / interrupt 恢复）
    CHECKPOINT_DB_PATH: str = str(BASE_DIR / "labpilot_checkpoints.db")

    # LLM（OpenAI 兼容）
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    DEFAULT_LLM_MODEL: str = "gpt-4o-mini"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS: int = 1536
    LLM_TEMPERATURE: float = 0.2
    MAX_TOKENS: int = 4000
    LLM_TIMEOUT_SECONDS: float = Field(default=30, gt=0, le=300)
    LLM_MAX_RETRIES: int = Field(default=1, ge=0, le=3)
    AGENT_RECURSION_LIMIT: int = Field(default=32, ge=2, le=100)
    AGENT_MAX_LLM_CALLS: int = Field(default=8, ge=1, le=30)
    AGENT_MAX_TOOL_CALLS: int = Field(default=16, ge=1, le=100)
    AGENT_RUN_SECONDS: float = Field(default=180, gt=0, le=1800)
    CHAT_MAX_CONCURRENT: int = Field(default=8, ge=1, le=100)
    CHAT_MAX_PER_USER: int = Field(default=2, ge=1, le=10)
    CHAT_STREAM_QUEUE_SIZE: int = Field(default=64, ge=2, le=1024)
    CHAT_REQUESTS_PER_MINUTE: int = Field(default=20, ge=1)
    AUTH_REQUESTS_PER_MINUTE: int = Field(default=20, ge=1)
    REMINDER_SCHEDULER_ENABLED: bool = True

    # 多用户 Zotero：LabPilot 按用户加密保存 Zotero API Key，并经 MCP sidecar 检索。
    # INTEGRATION_SECRET 必须是独立生成、持久保存的 Fernet key；轮换后旧密文无法解密。
    INTEGRATION_SECRET: str = ""
    # 仅 LabPilot 与 MCP sidecar 共享，防止其他容器直接调用持有用户凭证的服务。
    ZOTERO_MCP_INTERNAL_TOKEN: str = ""
    ZOTERO_MCP_URL: str = ""
    ZOTERO_MCP_TIMEOUT_SECONDS: float = 10.0

    # RAG（对齐 FastGraph 优化版：递归切分 + 混合检索 + 重排降级）
    RAG_CHUNK_SIZE: int = 500
    RAG_CHUNK_OVERLAP: int = 80
    RAG_TOP_K: int = 10
    RAG_RERANK_TOP_N: int = 3
    RAG_RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RAG_RERANK_URL: str = "https://api.siliconflow.cn/v1/rerank"
    RAG_USE_RERANK: bool = True
    # 向量余弦低于该阈值视为无关，避免 Top-K 恒返回
    RAG_MIN_SCORE: float = 0.22
    # 文档入库 embedding 的并发上限；防止大批量上传打爆 embedding API 限流
    RAG_EMBED_CONCURRENCY: int = 8

    # SMTP（仅审批通过后由执行器调用）
    EMAIL_SMTP_HOST: str = "smtp.qq.com"
    EMAIL_SMTP_PORT: int = 465
    EMAIL_SMTP_USER: str = ""
    EMAIL_SMTP_PASSWORD: str = ""
    EMAIL_SMTP_USE_SSL: bool = True
    # SMTP 握手（EHLO/HELO）使用的本机名；留空则自动探测，
    # 探测结果含非 ASCII（如中文计算机名）时回退 localhost。
    # Docker 中默认容器 ID 即可；企业邮件服务器若校验主机名可在此显式指定。
    EMAIL_SMTP_LOCAL_HOSTNAME: str = ""
    EMAIL_FROM_NAME: str = "LabPilot"
    # mock | smtp
    MAIL_BACKEND: str = "mock"

    # 审批
    APPROVAL_TTL_HOURS: int = 72
    # 截止前提醒提前量（小时）；提醒外发仍走审批
    REMIND_BEFORE_HOURS: int = 24
    REMIND_DEFAULT_TO: str = ""

    LOG_LEVEL: str = "INFO"

    def validate_runtime(self) -> None:
        """启动即拒绝不安全配置；错误只描述字段，不包含密钥值。"""
        if self.APP_ENV.strip().lower() not in {"production", "prod"}:
            return
        secret = self.JWT_SECRET_KEY.strip()
        if len(secret) < 32 or any(
            word in secret.lower() for word in ("dev-insecure", "change-me", "changeme")
        ):
            raise RuntimeError(
                "Production requires an independent JWT_SECRET_KEY of at least 32 characters"
            )
        if self.ZOTERO_MCP_URL.strip():
            from cryptography.fernet import Fernet

            try:
                Fernet(self.INTEGRATION_SECRET.strip().encode("ascii"))
            except (ValueError, UnicodeError):
                raise RuntimeError(
                    "Zotero integration requires a valid INTEGRATION_SECRET"
                ) from None
            token = self.ZOTERO_MCP_INTERNAL_TOKEN.strip()
            if (
                token == secret
                or len(token) < 32
                or any(w in token.lower() for w in ("change-me", "changeme"))
            ):
                raise RuntimeError(
                    "Zotero integration requires an independent internal token of at least 32 characters"
                )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime()
    return settings
