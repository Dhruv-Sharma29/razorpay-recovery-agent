from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    nim_api_key: str = ""
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_model: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    database_url: str = "sqlite:///./recovery.db"
    auto_recovery_amount_limit: int = 500000
    executor_mode: str = "mock"  # "mock" | "razorpay_test"
    environment: str = "development"
    api_secret_key: str = ""
    # Comma-separated list of browser origins allowed to call the API.
    # Wildcards are intentionally avoided: a specific allow-list is required
    # for credentialed CORS requests to work at all.
    cors_allow_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


settings = Settings()
