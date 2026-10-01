from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    llm_base_url: str = "https://aicredits.in/v1"
    llm_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"
    llm_timeout_s: float = 60.0
    llm_wall_s: float = 90.0
    llm_max_tokens: int = 2000
    llm_turn_token_cap: int = 12000
    max_user_active_turns: int = 2
    max_active_turns: int = 16
    executor_socket: str = ""
    # "fake" selects the scripted demo provider (E2E only); anything else is the real one.
    llm_provider: str = "real"

    app_secret_key: str = "dev-only-change-me"  # noqa: S105 - overridden via .env
    data_dir: Path = Path("./var")
    max_upload_mb: int = 50
    demo_reference_date: str = ""
    # Docker overrides this; the default is data/sample at the repository root.
    sample_dir: Path = Path(__file__).resolve().parents[2] / "data" / "sample"

    admin_email: str = ""
    admin_password: str = ""

    # executor limits (PLAN.md section 3)
    query_timeout_s: float = 10.0
    worker_memory_mb: int = 1024
    result_row_cap: int = 1000


@lru_cache
def get_settings() -> Settings:
    return Settings()
