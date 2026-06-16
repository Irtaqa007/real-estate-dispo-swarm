from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # App
    app_name: str = "RealEstateDispoSwarm"
    debug: bool = True
    version: str = "1.0.0"
    environment: str = "development"
    frontend_url: str = "http://localhost:3000"

    # Supabase
    supabase_url: Optional[str] = None
    supabase_key: Optional[str] = None
    supabase_service_key: Optional[str] = None

    # Groq
    groq_api_key: Optional[str] = None
    groq_model: str = "llama-3.3-70b-versatile"

    # Cohere
    cohere_api_key: Optional[str] = None

    # Gmail
    gmail_address: Optional[str] = None
    gmail_app_password: Optional[str] = None

    # Google Drive
    google_drive_client_id: Optional[str] = None
    google_drive_client_secret: Optional[str] = None
    google_drive_refresh_token: Optional[str] = None

    # Title company
    title_company_email: Optional[str] = None

    # Public base URL for unsubscribe links (e.g. https://api.example.com)
    base_url: str = "http://localhost:8000"

    # Unsubscribe HMAC secret — set a dedicated value for stability across restart
    # If not set, it is derived from database_url.
    unsubscribe_secret: Optional[str] = None

    # SSL mode for database connections ("require", "prefer", "disable", etc.)
    database_ssl_mode: str = "require"

    # Database — must be set via DATABASE_URL in .env or environment
    database_url: str

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")


settings = Settings()
