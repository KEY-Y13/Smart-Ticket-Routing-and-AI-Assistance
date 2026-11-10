import os
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SMARTTICKET_",
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
        secrets_dir=os.environ.get("SMARTTICKET_SECRETS_DIR"),
    )

    env: Literal["development", "test", "production"] = "development"
    classifier_manifest: Path | None = None
    artifact_root: Path = Path("artifacts")
    worker_heartbeat_path: Path = Path("artifacts/operations/worker.json")
    worker_stale_seconds: int = Field(default=180, ge=30, le=3600)
    queue_warning_seconds: int = Field(default=120, ge=30, le=3600)
    llm_enabled: bool = False
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("SMARTTICKET_LLM_API_KEY", "DASHSCOPE_API_KEY"),
    )
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_call_budget: int = Field(default=0, ge=0, le=1000)
    retrieval_url: str = "http://127.0.0.1:8011"
    retrieval_secret: SecretStr | None = None
    database_url: SecretStr | None = None
    jwt_secret: SecretStr | None = None
    token_minutes: int = Field(default=30, ge=1, le=120)
    jwt_issuer: str = "smartticket-ai"
    jwt_audience: str = "smartticket-web"

    @model_validator(mode="before")
    @classmethod
    def empty_manifest_is_unconfigured(cls, values):
        if isinstance(values, dict) and values.get("classifier_manifest") == "":
            values["classifier_manifest"] = None
        return values

    @model_validator(mode="after")
    def prevent_unimplemented_features(self) -> "Settings":
        if self.llm_enabled and (not self.llm_api_key or not self.llm_call_budget):
            raise ValueError("启用 LLM 需要本地密钥和明确非零调用上限")
        from urllib.parse import urlparse

        llm_url = urlparse(self.llm_base_url)
        if llm_url.scheme != "https" or llm_url.username or llm_url.query or llm_url.fragment:
            raise ValueError("LLM 地址必须为不含凭据的 HTTPS 地址")
        retrieval = urlparse(self.retrieval_url)
        if retrieval.scheme != "http" or retrieval.hostname not in {"127.0.0.1", "retrieval"}:
            raise ValueError("检索服务只允许本机或 Compose 内网服务")
        if self.database_url:
            url = make_url(self.database_url.get_secret_value())
            if url.get_backend_name() == "sqlite":
                if self.env != "test":
                    raise ValueError("SQLite 仅允许隔离测试，不作为 MySQL 部署替代")
            elif url.drivername != "mysql+pymysql" or url.database not in {
                "smartticket_ai",
                "smartticket_ai_test",
            }:
                raise ValueError("只允许专用 smartticket_ai 或 smartticket_ai_test 库")
            if not self.jwt_secret or len(self.jwt_secret.get_secret_value()) < 32:
                raise ValueError("配置数据库时必须提供至少 32 字符的独立 JWT 密钥")
        return self
