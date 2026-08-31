from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:latest"
    database_url: str = "sqlite:///./recovery.db"
    auto_recovery_amount_limit: int = 500000


settings = Settings()
