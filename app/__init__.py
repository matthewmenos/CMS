"""
COP Agona Ahanta — Flask application factory.
"""

import os
import logging
from datetime import datetime, timezone

from flask import Flask
from flask_login import LoginManager
from dotenv import load_dotenv

from app.models import db, GlobalUser, Church, init_tenant_db

load_dotenv()

login_manager = LoginManager()


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        SQLALCHEMY_DATABASE_URI=(
            "sqlite:///"
            + os.path.join(app.instance_path, "global.db")
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={
            "pool_pre_ping": True,
            "connect_args": {"check_same_thread": False},
        },
        TENANT_DB_DIR=os.path.join(app.instance_path, "tenants"),
        CHURCH_SLUG=os.environ.get("CHURCH_SLUG", "cop-agona-ahanta"),
        CHURCH_NAME=os.environ.get("CHURCH_NAME", "COP Agona Ahanta"),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,   # 16 MB hard cap on form uploads
    )

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["TENANT_DB_DIR"], exist_ok=True)

    # ------------------------------------------------------------------
    # Extensions
    # ------------------------------------------------------------------
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."

    @login_manager.user_loader
    def load_user(user_id: str):
        return GlobalUser.query.get(int(user_id))

    # ------------------------------------------------------------------
    # Database bootstrap
    # ------------------------------------------------------------------
    with app.app_context():
        db.create_all()
        _seed_defaults(app)

    # ------------------------------------------------------------------
    # Blueprints
    # ------------------------------------------------------------------
    from app.blueprints.auth.routes  import auth_bp
    from app.blueprints.api.routes   import api_bp
    from app.blueprints.admin.routes import admin_bp

    app.register_blueprint(auth_bp,  url_prefix="/auth")
    app.register_blueprint(api_bp,   url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # Main SPA route — serves the shell HTML for every non-API path
    from app.blueprints.main import main_bp
    app.register_blueprint(main_bp)

    # ------------------------------------------------------------------
    # Teardown: close tenant DB + async sync
    # ------------------------------------------------------------------
    from app.utils.tenant import close_tenant_db
    app.teardown_appcontext(close_tenant_db)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    return app


def _seed_defaults(app: Flask) -> None:
    """Ensure the default church tenant and admin user exist."""
    slug = os.environ.get("CHURCH_SLUG", "cop-agona-ahanta")
    church = Church.query.filter_by(slug=slug).first()

    if church is None:
        church = Church(
            name=os.environ.get("CHURCH_NAME", "COP Agona Ahanta"),
            slug=slug,
            db_key=f"dbs/{slug}.db",
        )
        db.session.add(church)
        db.session.flush()

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@copagonaahanta.org")
    if not GlobalUser.query.filter_by(email=admin_email).first():
        admin = GlobalUser(
            church_id=church.id,
            username=os.environ.get("ADMIN_USERNAME", "admin"),
            email=admin_email,
            role="admin",
        )
        admin.set_password(os.environ.get("ADMIN_PASSWORD", "Admin@1234"))
        db.session.add(admin)

    db.session.commit()

    # Initialise the tenant SQLite file
    tenant_path = os.path.join(
        app.config["TENANT_DB_DIR"], f"{slug}.db"
    )
    if not os.path.exists(tenant_path):
        init_tenant_db(tenant_path)
