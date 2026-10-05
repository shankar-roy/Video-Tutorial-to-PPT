from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str = "dev-secret-change-me"
    database_url: str = "sqlite:///./traveljournal.db"
    apple_client_id: str = "com.family.traveljournal"
    google_client_id: str = ""
    google_client_secret: str = ""
    public_base_url: str = "http://127.0.0.1:8000"
    allow_dev_login: bool = False
    media_root: str = "./media"
    app_env: str = "development"
    gmail_message_limit: int = 200
