"""
config.py — Environment-driven configuration for COP Agona Ahanta ChMS.

Three tiers:
  DevelopmentConfig  — SQLite, debug on, simple in-memory cache
  ProductionConfig   — PostgreSQL-ready, Redis cache, strict security
  TestingConfig      — In-memory SQLite, testing flags on
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


class BaseConfig:
    # ── Core ─────────────────────────────────────────────────────────────────
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-jwt-key-change-me")
    JWT_ACCESS_TOKEN_EXPIRES = 86400  # 24 hours

    # ── Global DB (auth + tenant registry) ───────────────────────────────────
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'instance' / 'global.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,          # Detect stale connections
        "pool_recycle": 300,            # Recycle every 5 min
        "connect_args": {
            "check_same_thread": False,  # SQLite threading
            "timeout": 20,
        }
    }

    # ── Tenant DB local cache directory ──────────────────────────────────────
    TENANT_DB_DIR = BASE_DIR / "instance" / "tenants"

    # ── Cloudflare R2 ─────────────────────────────────────────────────────────
    R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
    R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    R2_ENDPOINT_URL = os.environ.get(
        "R2_ENDPOINT_URL",
        f"https://{os.environ.get('R2_ACCOUNT_ID', '')}.r2.cloudflarestorage.com"
    )
    R2_MEDIA_BUCKET = os.environ.get("R2_MEDIA_BUCKET", "cop-agona-media")
    R2_DB_BUCKET = os.environ.get("R2_DB_BUCKET", "cop-agona-databases")
    R2_PRESIGN_EXPIRY = 3600  # 1 hour for presigned URLs

    # ── Church Identity ───────────────────────────────────────────────────────
    CHURCH_NAME = os.environ.get("CHURCH_NAME", "COP Agona Ahanta")
    CHURCH_SLUG = os.environ.get("CHURCH_SLUG", "cop-agona-ahanta")
    CHURCH_TAGLINE = os.environ.get("CHURCH_TAGLINE", "A Place of Grace, Growth & Glory")
    CHURCH_LOGO_URL = os.environ.get("CHURCH_LOGO_URL", "/static/icons/logo.png")

    # ── Admin ─────────────────────────────────────────────────────────────────
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@copagonaahanta.org")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")
    ADMIN_NAME = os.environ.get("ADMIN_NAME", "Church Admin")

    # ── Upload limits ─────────────────────────────────────────────────────────
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024   # 10 MB (for metadata; media goes direct to R2)
    ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime"}
    ALLOWED_AUDIO_TYPES = {"audio/mpeg", "audio/wav", "audio/ogg"}

    # ── Cache ─────────────────────────────────────────────────────────────────
    CACHE_TYPE = os.environ.get("CACHE_TYPE", "SimpleCache")
    CACHE_DEFAULT_TIMEOUT = 300  # 5 minutes

    # ── CORS ─────────────────────────────────────────────────────────────────
    CORS_ORIGINS = ["*"]  # Tighten in production


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    TESTING = False
    CACHE_TYPE = "SimpleCache"


class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False
    CACHE_TYPE = os.environ.get("CACHE_TYPE", "RedisCache")
    CACHE_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    # Force HTTPS
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"


class TestingConfig(BaseConfig):
    DEBUG = True
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CACHE_TYPE = "SimpleCache"
    WTF_CSRF_ENABLED = False


# ── Config map ────────────────────────────────────────────────────────────────
config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}
