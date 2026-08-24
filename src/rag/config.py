from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    """Not required today -- see agentsys/config.py's setting of the same
    name for why it's present unused (routing through litellm.py means
    pointing llm_model at an "anthropic/..." model is a config change, not
    a code change)."""
    openrouter_api_key: str = ""
    chroma_persist_dir: str = str(PROJECT_ROOT / "data" / "chroma")
    llm_model: str = "openai/gpt-4o-mini"
    embedding_model: str = "openai/text-embedding-3-small"
    vision_model: str = "openai/gpt-4o-mini"
    """Model used to turn an uploaded image into text (loaders._load_image) --
    gpt-4o-mini is already vision-capable, so this defaults to the same model
    as llm_model rather than requiring a second API/key. Kept as its own
    setting (not hardcoded) so pointing it at a stronger vision model later
    is a config change, not a code change -- same reasoning as
    agentsys/config.py's reviewer_llm_model."""
    image_max_dimension_px: int = 1600
    """Longest-edge cap an uploaded image is resized to before the vision
    call -- keeps the base64 payload (and the API's per-image token cost)
    bounded regardless of how large the source file is; 1600px is comfortably
    past the resolution vision models actually use internally."""

    cors_allowed_origins: str = "http://localhost:3000"
    """Comma-separated, not a JSON list -- matches agentsys/config.py's setting
    of the same name/shape, same reasoning: easy to type into a deployment
    platform's env-var UI without JSON-array syntax."""

    redis_url: str = "redis://localhost:6379/0"
    """Must point at the SAME Redis instance agentsys uses (see that
    package's config.py) -- sessions are created by agentsys's Google
    sign-in flow and validated here by rag/auth.py reading the same
    `session:{token}` keys. The two packages still don't import each other
    (see docs/MERGE.md) -- rag/auth.py duplicates the cookie-validation
    logic rather than importing agentsys.auth."""
    session_cookie_name: str = "af_session"
    session_cookie_secure: bool = False
    """Must match agentsys's setting of the same name -- it decides whether
    the cookie agentsys SETS carries the __Host- prefix, and this service
    has to look for the same name agentsys wrote. Mirrored rather than
    shared for the same reason the whole of rag/auth.py is (see
    docs/MERGE.md); a mismatch means this service reads a cookie name
    nothing ever sets, and every browser-facing endpoint here 401s."""

    @property
    def session_cookie(self) -> str:
        """Mirror of agentsys.config.Settings.session_cookie -- see that
        property for why the __Host- prefix matters. The two must agree, so
        the logic is duplicated verbatim rather than approximated."""
        if self.session_cookie_secure:
            return f"__Host-{self.session_cookie_name}"
        return self.session_cookie_name

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def raw_data_dir(self) -> Path:
        return PROJECT_ROOT / "data" / "raw"

    @property
    def processed_data_dir(self) -> Path:
        return PROJECT_ROOT / "data" / "processed"

    @property
    def vision_cache_dir(self) -> Path:
        """Sidecar cache for image->text results, keyed by content hash (see
        loaders._load_image). Without this, the same image gets re-sent to the
        vision API on every /v1/documents/upload AND again on every
        /v1/ingest -- a real, avoidable cost this cache exists specifically
        to remove, not a general-purpose optimization."""
        return PROJECT_ROOT / "data" / "processed" / "vision_cache"


settings = Settings()
