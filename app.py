"""
app.py — Flask application factory for COP Agona Ahanta ChMS.

Usage:
    flask run                     # development
    gunicorn "app:create_app()"   # production
"""

import os
import logging
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_cors import CORS
from flask_caching import Cache

from config import config
from models import db
from utils.auth import init_jwt, register_login_manager
from utils.db_router import register_db_sync_hooks

# ── Extensions (instantiated here, initialised in factory) ───────────────────
migrate      = Migrate()
login_manager = LoginManager()
cache        = Cache()


def create_app(env: str = None) -> Flask:
    """
    Application factory.

    Args:
        env: Config name — 'development', 'production', 'testing'.
             Falls back to FLASK_ENV environment variable, then 'default'.
    """
    env = env or os.environ.get("FLASK_ENV", "default")
    app = Flask(__name__, instance_relative_config=True)

    # ── Load config ───────────────────────────────────────────────────────────
    app.config.from_object(config[env])

    # Ensure instance folder exists
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["TENANT_DB_DIR"]).mkdir(parents=True, exist_ok=True)

    # ── Logging ───────────────────────────────────────────────────────────────
    _configure_logging(app)

    # ── Extensions ────────────────────────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    cache.init_app(app)
    CORS(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}})
    init_jwt(app)
    register_login_manager(login_manager, app)
    register_db_sync_hooks(app)

    # ── Blueprints ────────────────────────────────────────────────────────────
    _register_blueprints(app)

    # ── Shell context ─────────────────────────────────────────────────────────
    @app.shell_context_processor
    def _shell_ctx():
        from models import User, Church, Notification
        return {"db": db, "User": User, "Church": Church, "Notification": Notification}

    # ── CLI commands ──────────────────────────────────────────────────────────
    _register_cli(app)

    # ── PWA manifest ─────────────────────────────────────────────────────────
    @app.route("/manifest.json")
    def _manifest():
        return send_from_directory(app.static_folder, "manifest.json")

    @app.route("/sw.js")
    def _sw():
        return send_from_directory(
            os.path.join(app.static_folder, "js"), "sw.js",
            mimetype="application/javascript"
        )

    # ── Health check ──────────────────────────────────────────────────────────
    @app.route("/health")
    def _health():
        return jsonify({"status": "ok", "app": app.config["CHURCH_NAME"]}), 200

    # ── Global error handlers ─────────────────────────────────────────────────
    @app.errorhandler(404)
    def _404(_e):
        return jsonify({"error": "Not found."}), 404

    @app.errorhandler(500)
    def _500(e):
        app.logger.exception("Internal server error: %s", e)
        return jsonify({"error": "Internal server error."}), 500

    return app


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _register_blueprints(app: Flask) -> None:
    from blueprints.api.routes import api_bp
    from blueprints.admin.routes import admin_bp
    from blueprints.auth.routes import auth_bp
    from blueprints.app.routes import app_bp

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
    # Silence noisy libraries in production
    if not app.config.get("DEBUG"):
        logging.getLogger("boto3").setLevel(logging.WARNING)
        logging.getLogger("botocore").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)


def _register_cli(app: Flask) -> None:
    import click

    @app.cli.command("seed-admin")
    def seed_admin():
        """Create the superadmin user and default church tenant."""
        from models import User, Church
        from utils.db_router import ensure_tenant_db_schema

        # Global admin user
        if not User.query.filter_by(email=app.config["ADMIN_EMAIL"]).first():
            admin = User(
                email=app.config["ADMIN_EMAIL"],
                username="admin",
                display_name=app.config["ADMIN_NAME"],
                role="superadmin",
            )
            admin.set_password(app.config["ADMIN_PASSWORD"])
            db.session.add(admin)

        # Default church tenant
        slug = app.config["CHURCH_SLUG"]
        if not Church.query.filter_by(slug=slug).first():
            church = Church(
                name=app.config["CHURCH_NAME"],
                slug=slug,
                tagline=app.config.get("CHURCH_TAGLINE", ""),
                db_r2_key=f"{slug}.db",
            )
            db.session.add(church)

        db.session.commit()

        # Bootstrap tenant schema
        with app.app_context():
            ensure_tenant_db_schema(slug)

        click.echo(f"✓ Admin user and church '{slug}' seeded successfully.")

    @app.cli.command("sync-dbs")
    def sync_dbs():
        """Manually sync all dirty tenant DBs to R2."""
        from utils.db_router import sync_dirty_tenants
        n = sync_dirty_tenants()
        click.echo(f"✓ Synced {n} tenant database(s) to R2.")
