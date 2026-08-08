from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    openai_api_key: str = ""
    openrouter_api_key: str = ""
    chroma_persist_dir: str = str(PROJECT_ROOT / "data" / "chroma")
    llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"

    cors_allowed_origins: str = "http://localhost:3000"
    """Comma-separated, not a JSON list -- matches agentsys/config.py's setting
    of the same name/shape, same reasoning: easy to type into a deployment
    platform's env-var UI without JSON-array syntax."""

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def raw_data_dir(self) -> Path:
        return PROJECT_ROOT / "data" / "raw"

    @property
    def processed_data_dir(self) -> Path:
        return PROJECT_ROOT / "data" / "processed"


settings = Settings()
