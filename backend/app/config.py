from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    llm_provider: str = "gemini"
    aws_region: str = "ap-south-1"
    bedrock_model_id: str = "anthropic.claude-3-haiku-20240307-v1:0"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    chroma_path: str = str(ROOT / "storage" / "chroma")
    storage_path: str = str(ROOT / "storage")
    database_path: str = str(ROOT / "storage" / "template_hub.db")
    data_path: str = str(ROOT / "data")
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    embedding_model: str = "all-MiniLM-L6-v2"
    app_name: str = "YMSLI Template Hub"

    # Microsoft Graph / OneDrive
    graph_mode: str = "mock"  # mock | live
    azure_tenant_id: str = "common"
    azure_client_id: str = ""
    azure_client_secret: str = ""
    azure_redirect_uri: str = "http://localhost:5173"
    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    onedrive_root_folder: str = "YMSLI-Template-Hub"
    graph_scopes: str = "User.Read Files.ReadWrite.All offline_access"

    # Email/password auth
    jwt_secret: str = "ymsli-dev-secret-change-me"
    jwt_ttl_seconds: int = 604800

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def graph_scope_list(self) -> list[str]:
        return [s.strip() for s in self.graph_scopes.split() if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
