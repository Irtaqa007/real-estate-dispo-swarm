from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
from urllib.parse import urlparse


class Settings(BaseSettings):
    # App
    app_name: str = "RealEstateDispoSwarm"
    debug: bool = False
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
    groq_fallback_model: str = "llama-3.1-8b-instant"

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

    # Database connection timeout in seconds (default 30)
    database_connect_timeout: int = 30

    # Database command/query timeout in seconds (default 60)
    database_command_timeout: int = 60

    # Database — must be set via DATABASE_URL in .env or environment
    database_url: str

    # HuggingFace token for gated models (e.g., sentence-transformers)
    hf_token: Optional[str] = None

    # Force IPv4 when connecting to the database — resolves the hostname to an
    # IPv4 address before connecting. Set to False if your network supports IPv6
    # connections to your database host.
    force_ipv4: bool = True

    # Matching similarity threshold — minimum cosine similarity score for a
    # buyer-deal match to be considered valid. Below this threshold, no match.
    match_similarity_threshold: float = 0.65

    # Minimum number of verified, active, matched buyers required before a
    # campaign can be launched for a deal. If fewer match, the launch is
    # blocked with a structured message and an activity log entry is created.
    min_verified_buyers_to_launch: int = 50

    # Gmail daily send cap — hard ceiling before Gmail's ~500/day limit.
    # Campaign sends (send_type="campaign") are blocked when count reaches cap.
    # Reply sends (send_type="reply") are never blocked.
    gmail_daily_cap: int = 400

    # Timezone for midnight reset of the daily send counter.
    gmail_timezone: str = "Asia/Karachi"

    # Operator Identity — the AI speaks as this person in all communications.
    operator_name: str = ""
    operator_first_name: str = ""
    operator_email_signature: str = ""
    operator_tone: str = "conversational"
    operator_never_say: str = ""
    operator_context: str = ""

    # Ghost detection and recovery settings
    ghost_silence_hours: int = 96
    ghost_max_recovery_touches: int = 5
    ghost_recovery_intervals_days: list[int] = [4, 7, 12, 18, 25]

    # Auto-match settings: background scheduler task that matches all active
    # deals against all eligible buyers and auto-launches campaigns.
    # Set to False to disable the auto-match job (e.g. during debugging).
    auto_match_enabled: bool = True

    # How often the auto-match job runs, in hours.
    auto_match_interval_hours: int = 6

    # Scheduler task intervals (in minutes)
    reply_check_interval_minutes: int = 5
    hourly_check_interval_minutes: int = 60

    # IMPORTANT: Review market_adjuster.py and jv_rotator.py
    # logic and thresholds before enabling. These services
    # make autonomous suggestions that affect deal pricing
    # and JV partner selection. Disabled by default.
    market_adjuster_enabled: bool = False
    jv_rotator_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @property
    def database_host(self) -> Optional[str]:
        """Parse the hostname from DATABASE_URL for connectivity testing."""
        try:
            # Normalize: handle both postgresql:// and postgresql+asyncpg:// schemes
            url = self.database_url.replace("postgresql+asyncpg://", "postgresql://")
            parsed = urlparse(url)
            return parsed.hostname
        except Exception:
            return None

    @property
    def database_port(self) -> int:
        """Parse the port from DATABASE_URL."""
        try:
            url = self.database_url.replace("postgresql+asyncpg://", "postgresql://")
            parsed = urlparse(url)
            return parsed.port or 5432
        except Exception:
            return 5432


settings = Settings()
