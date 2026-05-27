"""Application configuration via pydantic-settings."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App-level configuration. Values can be overridden via environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="PYDBPLAY_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Directory where pydbplay stores its own data (SQLite, Fernet key, etc.)
    app_dir: Path = Path.home() / ".pydbplay"

    # Filename of the app-internal SQLite database (relative to app_dir)
    db_path: str = "app.db"

    # Server binding — MUST remain 127.0.0.1; never 0.0.0.0 (holds prod credentials)
    host: str = "127.0.0.1"
    port: int = 7777

    # Security — Host/Origin allowlist + CSRF double-submit cookie
    security_enabled: bool = True
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "::1", "[::1]"]

    @field_validator("app_dir", mode="after")
    @classmethod
    def ensure_app_dir(cls, v: Path) -> Path:
        """Create app_dir on first access if it doesn't exist.

        The directory holds sensitive data (Fernet key, encrypted passwords)
        so it is restricted to the owner only (0o700).  chmod is called even
        when the directory already exists so a pre-existing world-readable dir
        is tightened automatically.
        """
        v.mkdir(parents=True, exist_ok=True)
        v.chmod(0o700)
        return v

    @property
    def db_file(self) -> Path:
        """Absolute path to the SQLite database file."""
        return self.app_dir / self.db_path


def get_settings() -> Settings:
    """Return a fresh Settings instance (reads env vars at call time)."""
    return Settings()
