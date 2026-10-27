"""Application configuration loaded from environment variables (and an optional .env file).

Every tunable limit lives here so the API, worker and tests agree on the same values.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Environment -----------------------------------------------------------------
    env: str = Field("development", alias="ANNOTATOR_ENV")  # development | test | production

    # --- AI -----------------------------------------------------------------------------
    anthropic_api_key: str | None = Field(None, alias="ANTHROPIC_API_KEY")
    # "anthropic" for real use. "fake" is a scripted provider that is only honoured when
    # ANNOTATOR_ENV=test, so the UI can never silently present scripted output as real.
    ai_provider: str = Field("anthropic", alias="ANNOTATOR_AI_PROVIDER")
    model: str = Field("claude-opus-5-5", alias="ANNOTATOR_MODEL")
    # Model used for short auxiliary calls (category detection from instructions).
    light_model: str = Field("claude-opus-5-5", alias="ANNOTATOR_LIGHT_MODEL")
    analysis_effort: str = Field("high", alias="ANNOTATOR_ANALYSIS_EFFORT")
    summary_effort: str = Field("medium", alias="ANNOTATOR_SUMMARY_EFFORT")
    light_effort: str = Field("low", alias="ANNOTATOR_LIGHT_EFFORT")
    refusal_fallback: bool = Field(True, alias="ANNOTATOR_REFUSAL_FALLBACK")
    ai_concurrency: int = Field(3, alias="ANNOTATOR_AI_CONCURRENCY")
    ai_max_attempts: int = Field(5, alias="ANNOTATOR_AI_MAX_ATTEMPTS")
    ai_max_output_tokens: int = Field(64000, alias="ANNOTATOR_AI_MAX_OUTPUT_TOKENS")
    # Documents whose prompt text is estimated above this many tokens are analysed in
    # sections after a document-wide planning pass.
    single_pass_token_limit: int = Field(150_000, alias="ANNOTATOR_SINGLE_PASS_TOKEN_LIMIT")
    section_token_target: int = Field(40_000, alias="ANNOTATOR_SECTION_TOKEN_TARGET")
    # Optional price overrides (USD per million tokens) for models not in the built-in table.
    price_input_per_mtok: float | None = Field(None, alias="ANNOTATOR_PRICE_INPUT_PER_MTOK")
    price_output_per_mtok: float | None = Field(None, alias="ANNOTATOR_PRICE_OUTPUT_PER_MTOK")

    # --- Limits -------------------------------------------------------------------------
    max_upload_mb: int = Field(50, alias="ANNOTATOR_MAX_UPLOAD_MB")
    max_pages: int = Field(50, alias="ANNOTATOR_MAX_PAGES")
    max_instruction_mb: int = Field(10, alias="ANNOTATOR_MAX_INSTRUCTION_MB")
    max_instruction_chars: int = Field(20_000, alias="ANNOTATOR_MAX_INSTRUCTION_CHARS")

    # --- Matching -----------------------------------------------------------------------
    # Minimum similarity (0-100) for accepting a quote that is not an exact text match.
    fuzzy_accept_score: float = Field(95.0, alias="ANNOTATOR_FUZZY_ACCEPT_SCORE")

    # --- Storage and retention ------------------------------------------------------------
    data_dir: Path = Field(REPO_ROOT / "data", alias="ANNOTATOR_DATA_DIR")
    retention_hours: int = Field(72, alias="ANNOTATOR_RETENTION_HOURS")
    keep_work_files: bool = Field(False, alias="ANNOTATOR_KEEP_WORK_FILES")

    # --- Worker -------------------------------------------------------------------------
    worker_poll_seconds: float = Field(1.0, alias="ANNOTATOR_WORKER_POLL_SECONDS")
    lease_seconds: int = Field(60, alias="ANNOTATOR_LEASE_SECONDS")
    max_job_attempts: int = Field(3, alias="ANNOTATOR_MAX_JOB_ATTEMPTS")

    # --- External tools -------------------------------------------------------------------
    tesseract_cmd: str | None = Field(None, alias="ANNOTATOR_TESSERACT_CMD")
    ocr_language: str = Field("eng", alias="ANNOTATOR_OCR_LANGUAGE")
    soffice_cmd: str | None = Field(None, alias="ANNOTATOR_SOFFICE_CMD")

    # --- API ------------------------------------------------------------------------------
    cors_origins: str = Field(
        "http://localhost:3000,http://127.0.0.1:3000", alias="ANNOTATOR_CORS_ORIGINS"
    )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_instruction_bytes(self) -> int:
        return self.max_instruction_mb * 1024 * 1024

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "annotator.db"

    @property
    def fake_ai_enabled(self) -> bool:
        return self.ai_provider == "fake" and self.env == "test"

    @property
    def ai_configured(self) -> bool:
        return self.fake_ai_enabled or bool(self.anthropic_api_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
