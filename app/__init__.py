"""
COP Agona Ahanta — Flask application factory.
"""

import atexit
import logging
import os

from flask import Flask
from flask_login import LoginManager
from dotenv import load_dotenv
from sqlalchemy.pool import NullPool

from app.models import db, GlobalUser, Church, init_tenant_db
from app.utils.r2 import download_global_db, upload_global_db_async

load_dotenv()

login_manager = LoginManager()
log = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    # Use a persistent database path (configurable via env var)
    # On Render.com, /var/data is persistent; fall back to instance/ for local dev
    _db_base = os.environ.get(
        "DATABASE_DIR",
        os.path.join(app.instance_path, "data"),
    )
    _global_db_path = os.path.join(_db_base, "global.db")
    _tenant_db_dir  = os.path.join(_db_base, "tenants")

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{_global_db_path}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # NullPool: SQLite doesn't benefit from connection pooling and
        # NullPool prevents the "unclosed database connection" ResourceWarning
        # that the default StaticPool/QueuePool causes at interpreter exit.
        SQLALCHEMY_ENGINE_OPTIONS={
            "poolclass": NullPool,
            "connect_args": {"check_same_thread": False},
        },
        TENANT_DB_DIR=_tenant_db_dir,
        CHURCH_SLUG=os.environ.get("CHURCH_SLUG", "cop-agona-ahanta"),
        CHURCH_NAME=os.environ.get("CHURCH_NAME", "COP Agona Ahanta"),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )

    os.makedirs(_db_base, exist_ok=True)
    os.makedirs(_tenant_db_dir, exist_ok=True)

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

    # Capture the engine reference now (inside app context) so the atexit
    # callback can dispose it without needing an active application context.
    with app.app_context():
        _engine = db.engine
    atexit.register(_engine.dispose)

    # ------------------------------------------------------------------
    # Global DB sync to/from R2 for persistence across deploys
    # ------------------------------------------------------------------
    # On startup: try to pull the global DB from R2 so we don't start
    # with a blank slate on a fresh instance.  If the download fails
    # (no R2 configured, first run, or network blip) we keep the local
    # file — db.create_all() / _seed_defaults() above already ran.
    _global_db_path = _global_db_path  # captured from config above
    download_global_db(_global_db_path)

    # On shutdown: push the global DB to R2 so the next instance picks
    # up where we left off.  This runs in a background thread so it
    # won't block interpreter exit.
    def _sync_global_db_on_exit():
        upload_global_db_async(_global_db_path)

    atexit.register(_sync_global_db_on_exit)

    # ------------------------------------------------------------------
    # Blueprints
    # ------------------------------------------------------------------
    from app.blueprints.auth.routes  import auth_bp
    from app.blueprints.api.routes   import api_bp
    from app.blueprints.admin.routes import admin_bp
    from app.blueprints.main         import main_bp

    app.register_blueprint(auth_bp,  url_prefix="/auth")
    app.register_blueprint(api_bp,   url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(main_bp)

    # ------------------------------------------------------------------
    # Teardown: close tenant DB per-request + queue async R2 sync
    # ------------------------------------------------------------------
    from app.utils.tenant import close_tenant_db
    app.teardown_appcontext(close_tenant_db)

    # ------------------------------------------------------------------
    # Security headers
    # ------------------------------------------------------------------
    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    return app


def _seed_defaults(app: Flask) -> None:
    """Ensure the default church and admin account exist on first run."""
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
        log.info("Created church record for slug '%s'", slug)

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
        log.info("Created admin account '%s'", admin_email)

    db.session.commit()

    # Initialise tenant SQLite file if it doesn't exist yet
    tenant_path = os.path.join(app.config["TENANT_DB_DIR"], f"{slug}.db")
    if not os.path.exists(tenant_path):
        init_tenant_db(tenant_path)
        log.info("Initialised tenant DB at %s", tenant_path)