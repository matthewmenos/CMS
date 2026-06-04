"""
config.py — Environment-driven configuration for COP Agona Ahanta ChMS.

SQLite dual-database layout:
  Global DB  → DATABASE_URL   (default: sqlite:////tmp/global.db on Render)
  Tenant DBs → TENANT_DB_DIR  (default: /tmp/tenants/<slug>.db on Render)

Both are backed up to / restored from Cloudflare R2 automatically.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# ── Detect Render environment ─────────────────────────────────────────────────
# Render sets the RENDER env var automatically on all its services.
ON_RENDER = bool(os.environ.get("RENDER"))

# On Render, /tmp is writable and survives within a running instance session.
# Locally, we use the instance/ folder beside the project.
_DEFAULT_GLOBAL_DB = (
    "sqlite:////tmp/global.db"
    if ON_RENDER
    else f"sqlite:///{BASE_DIR / 'instance' / 'global.db'}"
)
_DEFAULT_TENANT_DIR = (
    Path("/tmp/tenants")
    if ON_RENDER
    else BASE_DIR / "instance" / "tenants"
)


class BaseConfig:
    # ── Core ──────────────────────────────────────────────────────────────────
    SECRET_KEY     = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-jwt-change-me")
    JWT_ACCESS_TOKEN_EXPIRES = 86400  # 24 hours

    # ── Global SQLite DB (auth + tenant registry) ──────────────────────────────
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", _DEFAULT_GLOBAL_DB)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle":  300,
        "connect_args": {
            "check_same_thread": False,   # Required for SQLite multi-thread
            "timeout": 20,
        },
    }

    # ── Tenant DB local cache directory ───────────────────────────────────────
    TENANT_DB_DIR = Path(
        os.environ.get("TENANT_DB_DIR", str(_DEFAULT_TENANT_DIR))
    )

    # ── Cloudflare R2 ──────────────────────────────────────────────────────────
    R2_ACCOUNT_ID        = os.environ.get("R2_ACCOUNT_ID", "")
    R2_ACCESS_KEY_ID     = os.environ.get("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    R2_ENDPOINT_URL      = os.environ.get(
        "R2_ENDPOINT_URL",
        f"https://{os.environ.get('R2_ACCOUNT_ID', '')}.r2.cloudflarestorage.com",
    )
    R2_MEDIA_BUCKET  = os.environ.get("R2_MEDIA_BUCKET", "cop-agona-media")
    R2_DB_BUCKET     = os.environ.get("R2_DB_BUCKET",    "cop-agona-databases")
    R2_PRESIGN_EXPIRY = 3600  # seconds

    # ── Church Identity ───────────────────────────────────────────────────────
    CHURCH_NAME    = os.environ.get("CHURCH_NAME",    "COP Agona Ahanta")
    CHURCH_SLUG    = os.environ.get("CHURCH_SLUG",    "cop-agona-ahanta")
    CHURCH_TAGLINE = os.environ.get("CHURCH_TAGLINE", "A Place of Grace, Growth & Glory")
    CHURCH_LOGO_URL = os.environ.get("CHURCH_LOGO_URL", "/static/icons/logo.png")

    # ── Admin credentials ─────────────────────────────────────────────────────
    ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL",    "admin@copagonaahanta.org")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")
    ADMIN_NAME     = os.environ.get("ADMIN_NAME",     "Church Admin")

    # ── Upload limits ─────────────────────────────────────────────────────────
    MAX_CONTENT_LENGTH  = 10 * 1024 * 1024   # 10 MB via Flask (media goes direct to R2)
    ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime"}
    ALLOWED_AUDIO_TYPES = {"audio/mpeg", "audio/wav", "audio/ogg"}

    # ── Cache ─────────────────────────────────────────────────────────────────
    CACHE_TYPE            = os.environ.get("CACHE_TYPE", "SimpleCache")
    CACHE_DEFAULT_TIMEOUT = 300

    # ── CORS ──────────────────────────────────────────────────────────────────
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")


class DevelopmentConfig(BaseConfig):
    DEBUG   = True
    TESTING = False


class ProductionConfig(BaseConfig):
    DEBUG   = False
    TESTING = False
    # Tighten cookie security in production
    SESSION_COOKIE_SECURE   = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"


class TestingConfig(BaseConfig):
    DEBUG   = True
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CACHE_TYPE              = "SimpleCache"
    TENANT_DB_DIR           = Path("/tmp/test-tenants")


# ── Config map ────────────────────────────────────────────────────────────────
config = {
    "development": DevelopmentConfig,
    "production":  ProductionConfig,
    "testing":     TestingConfig,
    "default":     DevelopmentConfig,
}
