"""
Application configuration for APAAR Nexus.
Supports development, testing, and production environments.
Loads variables from .env via python-dotenv.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)

DEFAULT_DEV_SECRET = "dev-secret-key-change-in-production-12345"

# Resolve database URI to absolute path on Windows if default relative path is specified
_raw_db_url = os.getenv("DATABASE_URL", "sqlite:///instance/apaar_nexus.db")
if _raw_db_url in ("sqlite:///instance/apaar_nexus.db", "sqlite:///./instance/apaar_nexus.db"):
    _default_db_file = (INSTANCE_DIR / "apaar_nexus.db").as_posix()
    RESOLVED_DATABASE_URI = f"sqlite:///{_default_db_file}"
else:
    RESOLVED_DATABASE_URI = _raw_db_url


class Config:
    """Base configuration shared across environments."""

    SECRET_KEY = os.getenv("SECRET_KEY", DEFAULT_DEV_SECRET)
    SQLALCHEMY_DATABASE_URI = RESOLVED_DATABASE_URI
    SQLALCHEMY_TRACK_MODIFICATIONS = False


    # Demo accounts and auth
    DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "Demo@12345")

    # Ingestion and upload limits
    MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "2"))
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024
    MAX_CSV_ROWS = int(os.getenv("MAX_CSV_ROWS", "5000"))

    # Credential key storage
    SIGNING_KEY_DIR = os.getenv("SIGNING_KEY_DIR", "instance/keys")

    # Session cookie hardening
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in ("true", "1", "yes")

    # CSRF protection
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None

    # Demo reset toggle and login hints
    ALLOW_DEMO_RESET = os.getenv("ALLOW_DEMO_RESET", "true").lower() in ("true", "1", "yes")
    SHOW_DEMO_ACCOUNTS = os.getenv("SHOW_DEMO_ACCOUNTS", "true").lower() in ("true", "1", "yes")

    # App metadata
    APP_NAME = "APAARSetu"
    APP_VERSION = "1.0.0-prototype"
    SYNTHETIC_NOTICE = "Prototype — synthetic data only. Not connected to AICTE, APAAR, UIDAI or DigiLocker."


    @classmethod
    def init_app(cls, app):
        """Optional hook for extra initialization."""
        pass


class DevelopmentConfig(Config):
    """Development environment configuration."""
    DEBUG = True


class TestingConfig(Config):
    """Testing environment configuration."""
    TESTING = True
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")
    # CSRF configurable, disabled by default for simpler client test invocations
    WTF_CSRF_ENABLED = os.getenv("TEST_WTF_CSRF_ENABLED", "false").lower() in ("true", "1", "yes")


class ProductionConfig(Config):
    """Production configuration with strict security checks."""
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    SHOW_DEMO_ACCOUNTS = False

    @classmethod
    def init_app(cls, app):
        super().init_app(app)
        secret = os.getenv("SECRET_KEY")
        if not secret or secret == DEFAULT_DEV_SECRET:
            raise RuntimeError(
                "CRITICAL: ProductionConfig requires a strong, unique SECRET_KEY environment variable."
            )
        if os.getenv("DEMO_PASSWORD", "Demo@12345") == "Demo@12345":
            raise RuntimeError(
                "CRITICAL: ProductionConfig refuses to run with default DEMO_PASSWORD."
            )


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
