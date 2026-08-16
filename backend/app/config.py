from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    aws_region: str = "us-east-1"
    aws_s3_bucket: str = ""
    s3_bucket: str = "info-nexus-s3"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""
    aws_ca_bundle: str = ""
    aws_verify_ssl: bool = True
    aws_profile: str = ""

    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    bedrock_max_tokens: int = 4096
    bedrock_api_key: str = ""

    jwt_secret_key: str = ""
    jwt_secret: str = "ymsli-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_ttl_seconds: int = 604800

    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173"
    app_name: str = "YMSLI Template Hub"
    max_upload_bytes: int = 12 * 1024 * 1024

    s3_templates_prefix: str = "template/"
    s3_documents_prefix: str = "documents/generated/"
    s3_previews_prefix: str = "documents/previews/"
    s3_metadata_prefix: str = "metadata/template/"
    s3_kb_prefix: str = "KB/"

    @property
    def bucket(self) -> str:
        return (self.aws_s3_bucket or self.s3_bucket or "info-nexus-s3").strip()

    @property
    def jwt_secret_value(self) -> str:
        return (self.jwt_secret_key or self.jwt_secret or "ymsli-dev-secret-change-me").strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
