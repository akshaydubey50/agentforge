from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    database_url: str = "postgresql+psycopg://agent:agent@localhost:5432/agentsys"
    redis_url: str = "redis://localhost:6379/0"
    chroma_host: str = "localhost"
    chroma_port: int = 8001

    max_subtask_retries: int = 2
    plan_confidence_escalation_threshold: int = 3
    review_escalation_threshold: int = 2

    docker_sandbox_image: str = "python:3.11-slim"
    workspace_dir: str = str(PROJECT_ROOT / "data" / "workspace")


settings = Settings()
