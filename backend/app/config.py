from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+asyncpg://easo:easo@localhost:5435/easo"
    redis_url: str = "redis://localhost:6381/0"
    cors_origins: list[str] = ["http://localhost:5180"]

    # Auth
    jwt_secret: str = "change-me"
    jwt_ttl_days: int = 30
    telegram_bot_token: str = ""
    telegram_bot_username: str = (
        ""  # for the Login Widget; the site domain is set with /setdomain in @BotFather
    )
    telegram_auth_max_age_s: int = 86400
    google_client_id: str = ""  # OAuth Web client ID (Google Cloud Console)
    # E-mail login codes: "console" logs the code (development), "resend" sends through resend.com
    mail_provider: Literal["console", "resend"] = "console"
    resend_api_key: str = ""
    mail_from: str = "Easo <no-reply@example.com>"
    login_code_ttl_minutes: int = 10
    # Passwordless sign-in as any seeded user, for local testing only. Never in prod.
    dev_login: bool = False

    web_app_url: str = "http://localhost:8090"

    # Monitoring: unexpected errors are e-mailed here (empty = off), the same one at most once per cooldown
    alert_email: str = ""
    alert_cooldown_s: int = 900
    # Dead-man switch (healthchecks.io etc.): the scheduler GETs it every minute; empty = off
    healthcheck_ping_url: str = ""

    # Company registry used to prefill sign-up by TIN. There is no official public API in Armenia (docs/04):
    # "demo" = built-in fictional companies, "eregister" = public company search of e-register.moj.am
    # (name, status, registration number, address; cached a day), "none" = manual entry only.
    registry_provider: Literal["demo", "eregister", "none"] = "demo"
    eregister_url: str = "https://e-register.moj.am"
    # Address check (house -> district) via OpenStreetMap Nominatim; empty disables it
    geocoder_url: str = "https://nominatim.openstreetmap.org"

    # Uploaded verification files (local volume; S3/R2 later)
    media_dir: str = "media"
    max_upload_mb: int = 10

    # Marketplace defaults (sellers can override most of them, see docs/02-mvp.md §6)
    offer_response_hours: int = 24
    offer_max_rounds: int = 3
    offer_min_line_amd: int = 20_000
    auto_accept_after_delivery_hours: int = 48

    @model_validator(mode="after")
    def _prod_guards(self) -> "Settings":
        if self.env == "prod" and (self.jwt_secret == "change-me" or len(self.jwt_secret) < 32):
            raise ValueError("JWT_SECRET must be a random string of 32+ chars in prod")
        if self.env == "prod" and self.dev_login:
            raise ValueError("DEV_LOGIN must be off in prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
