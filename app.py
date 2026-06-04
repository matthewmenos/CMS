"""
app.py — Flask application factory for COP Agona Ahanta ChMS.

Render-ready SQLite dual-database setup:
  • global.db   — users, churches, notifications  (R2 key: "global.db")
  • <slug>.db   — per-church social data          (R2 key: "<slug>.db")

Cold-start flow:
  1. _restore_global_db()  — pulls global.db from R2 → /tmp/global.db
  2. db.create_all()       — creates tables if this is a brand-new DB
  3. _seed_if_empty()      — inserts superadmin + church on first ever boot
  4. teardown hook         — syncs global.db back to R2 after every write

Usage:
  flask run                      # local dev
  gunicorn "app:create_app()"    # production / Render
"""

import os
import logging
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from flask import Flask, jsonify, send_from_directory
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_cors import CORS
from flask_caching import Cache

from config import config
from models import db
from utils.auth import init_jwt, register_login_manager
from utils.db_router import register_db_sync_hooks

# ── Module-level extensions ───────────────────────────────────────────────────
migrate       = Migrate()
login_manager = LoginManager()
cache         = Cache()

# Track whether global.db was modified this request so we only sync when needed
_global_db_dirty = False


# ═════════════════════════════════════════════════════════════════════════════
# APPLICATION FACTORY
# ═════════════════════════════════════════════════════════════════════════════

def create_app(env: str = None) -> Flask:
    """
    Build and return the configured Flask application.

    Args:
        env: 'development' | 'production' | 'testing'.
             Falls back to FLASK_ENV env var, then 'default'.
    """
    env = env or os.environ.get("FLASK_ENV", "default")
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config[env])

    # ── Ensure writable directories exist ────────────────────────────────────
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["TENANT_DB_DIR"]).mkdir(parents=True, exist_ok=True)

    # ── Logging ───────────────────────────────────────────────────────────────
    _configure_logging(app)

    # ── Step 1: Restore global.db from R2 BEFORE db.init_app() ───────────────
    _restore_global_db(app)

    # ── Extensions ────────────────────────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    cache.init_app(app)
    CORS(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}})
    init_jwt(app)
    register_login_manager(login_manager, app)
    register_db_sync_hooks(app)      # syncs tenant DBs on teardown

    # ── Step 2 & 3: Create tables + seed once ─────────────────────────────────
    with app.app_context():
        db.create_all()
        _seed_if_empty(app)

    # ── Step 4: Sync global.db back to R2 on every request teardown ──────────
    @app.teardown_appcontext
    def _sync_global_on_teardown(exception):
        """Push global.db to R2 after any request that wrote to it."""
        if exception is not None:
            return
        global_db_path = _global_db_path(app)
        if not global_db_path.exists():
            return
        try:
            _r2_upload_global(app, global_db_path)
        except Exception as exc:
            app.logger.error("global.db R2 sync failed on teardown: %s", exc)

    # ── Blueprints ────────────────────────────────────────────────────────────
    _register_blueprints(app)

    # ── Shell context ─────────────────────────────────────────────────────────
    @app.shell_context_processor
    def _shell_ctx():
        from models import User, Church, Notification
        return {"db": db, "User": User, "Church": Church, "Notification": Notification}

    # ── CLI commands ──────────────────────────────────────────────────────────
    _register_cli(app)

    # ── PWA / static routes ───────────────────────────────────────────────────
    @app.route("/manifest.json")
    def _manifest():
        return send_from_directory(app.static_folder, "manifest.json")

    @app.route("/sw.js")
    def _sw():
        return send_from_directory(
            os.path.join(app.static_folder, "js"), "sw.js",
            mimetype="application/javascript",
        )

    # ── Health check ──────────────────────────────────────────────────────────
    @app.route("/health")
    def _health():
        return jsonify({"status": "ok", "app": app.config["CHURCH_NAME"]}), 200

    # ── Error handlers ────────────────────────────────────────────────────────
    @app.errorhandler(404)
    def _404(_e):
        return jsonify({"error": "Not found."}), 404

    @app.errorhandler(500)
    def _500(e):
        app.logger.exception("Internal server error: %s", e)
        return jsonify({"error": "Internal server error."}), 500

    app.logger.info("COP Agona Ahanta app ready [env=%s]", env)
    return app


# ═════════════════════════════════════════════════════════════════════════════
# GLOBAL DB — RESTORE & SYNC HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _global_db_path(app: Flask) -> Path:
    """Return the local path for global.db (from DATABASE_URL or instance folder)."""
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    # Strip sqlite:/// prefix to get the file path
    if uri.startswith("sqlite:////"):
        return Path(uri[len("sqlite:////"):])  # absolute path
    if uri.startswith("sqlite:///"):
        return Path(app.instance_path) / uri[len("sqlite:///"):]
    # Fallback
    return Path(app.instance_path) / "global.db"


def _r2_client_from_config(app: Flask):
    """Build a bare boto3 client from app config (no Flask context needed)."""
    return boto3.client(
        "s3",
        endpoint_url=app.config["R2_ENDPOINT_URL"],
        aws_access_key_id=app.config["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=app.config["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=BotoConfig(retries={"max_attempts": 3, "mode": "adaptive"}),
    )


def _restore_global_db(app: Flask) -> None:
    """
    Pull global.db from R2 into local filesystem on cold start.
    Runs BEFORE db.init_app() so SQLAlchemy always opens a valid (or new) file.

    - If R2 has global.db and local copy is stale → download it.
    - If R2 has no global.db yet → do nothing (db.create_all() will build it).
    - If R2 credentials are not set → skip silently (local dev without R2).
    """
    # Skip if R2 is not configured (local dev)
    if not app.config.get("R2_ACCESS_KEY_ID") or not app.config.get("R2_ENDPOINT_URL"):
        app.logger.info("R2 not configured — skipping global.db restore (local dev).")
        return

    db_path = _global_db_path(app)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        client = _r2_client_from_config(app)
        bucket = app.config["R2_DB_BUCKET"]

        # Check remote object exists
        try:
            head = client.head_object(Bucket=bucket, Key="global.db")
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                app.logger.info("global.db not in R2 yet — will create fresh.")
                return
            raise

        remote_etag = head.get("ETag", "").strip('"')

        # Skip download if local copy is already current
        if db_path.exists() and remote_etag:
            import hashlib
            h = hashlib.md5()
            with open(db_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            if h.hexdigest() == remote_etag:
                app.logger.info("global.db is up-to-date (ETag match).")
                return

        app.logger.info("Restoring global.db from R2 → %s …", db_path)
        client.download_file(bucket, "global.db", str(db_path))
        app.logger.info("global.db restored successfully.")

    except Exception as exc:
        # Non-fatal: if R2 is temporarily unreachable, start with local copy
        app.logger.warning(
            "Could not restore global.db from R2 (%s). "
            "Using local copy if present, or creating fresh.", exc
        )


def _r2_upload_global(app: Flask, db_path: Path) -> None:
    """Push global.db to R2. Skips if R2 is not configured."""
    if not app.config.get("R2_ACCESS_KEY_ID") or not app.config.get("R2_ENDPOINT_URL"):
        return

    client = _r2_client_from_config(app)
    bucket = app.config["R2_DB_BUCKET"]

    from datetime import datetime, timezone
    client.upload_file(
        str(db_path),
        bucket,
        "global.db",
        ExtraArgs={
            "ContentType": "application/x-sqlite3",
            "Metadata": {"uploaded-at": datetime.now(timezone.utc).isoformat()},
        },
    )
    app.logger.debug("global.db → R2 synced.")


# ═════════════════════════════════════════════════════════════════════════════
# AUTO-SEED  (runs once on first ever boot)
# ═════════════════════════════════════════════════════════════════════════════

def _seed_if_empty(app: Flask) -> None:
    """
    Insert the superadmin user and default church tenant if the DB is brand new.
    Safe to call on every startup — checks first, inserts only if empty.
    """
    from models import User, Church
    from utils.db_router import ensure_tenant_db_schema

    if User.query.first():
        app.logger.debug("DB already seeded — skipping.")
        return

    app.logger.info("First boot detected — seeding admin and church tenant…")

    slug = app.config["CHURCH_SLUG"]

    admin = User(
        email=app.config["ADMIN_EMAIL"],
        username="admin",
        display_name=app.config["ADMIN_NAME"],
        role="superadmin",
        is_active=True,
    )
    admin.set_password(app.config["ADMIN_PASSWORD"])
    db.session.add(admin)

    if not Church.query.filter_by(slug=slug).first():
        church = Church(
            name=app.config["CHURCH_NAME"],
            slug=slug,
            tagline=app.config.get("CHURCH_TAGLINE", ""),
            db_r2_key=f"{slug}.db",
            is_active=True,
        )
        db.session.add(church)

    db.session.commit()
    app.logger.info("Seeded superadmin (%s) and church '%s'.",
                    app.config["ADMIN_EMAIL"], slug)

    # Bootstrap the tenant schema (creates tables in <slug>.db)
    try:
        ensure_tenant_db_schema(slug)
        app.logger.info("Tenant schema bootstrapped for '%s'.", slug)
    except Exception as exc:
        app.logger.error("Tenant schema bootstrap failed: %s", exc)


# ═════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _register_blueprints(app: Flask) -> None:
    from blueprints.api.routes  import api_bp
    from blueprints.admin.routes import admin_bp
    from blueprints.auth.routes  import auth_bp
    from blueprints.app.routes   import app_bp

    app.register_blueprint(auth_bp,  url_prefix="/auth")
    app.register_blueprint(api_bp,   url_prefix="/api/v1")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(app_bp,   url_prefix="/")


def _configure_logging(app: Flask) -> None:
    level = logging.DEBUG if app.config.get("DEBUG") else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if not app.config.get("DEBUG"):
        for lib in ("boto3", "botocore", "urllib3", "s3transfer"):
            logging.getLogger(lib).setLevel(logging.WARNING)


def _register_cli(app: Flask) -> None:
    """Register Flask CLI commands for manual operations."""
    import click

    @app.cli.command("sync-dbs")
    def sync_dbs():
        """Manually push all dirty tenant DBs to R2."""
        from utils.db_router import sync_dirty_tenants
        n = sync_dirty_tenants()
        click.echo(f"✓ Synced {n} tenant database(s) to R2.")

    @app.cli.command("sync-global")
    def sync_global():
        """Manually push global.db to R2."""
        db_path = _global_db_path(app)
        if not db_path.exists():
            click.echo("✗ global.db not found locally.")
            return
        _r2_upload_global(app, db_path)
        click.echo(f"✓ global.db synced to R2 from {db_path}.")

    @app.cli.command("restore-global")
    def restore_global():
        """Pull global.db from R2 to local disk."""
        _restore_global_db(app)
        click.echo("✓ global.db restore attempted (check logs for result).")
