"""
utils/db_router.py — Multi-tenant SQLite routing engine.

Each church has its own isolated .db file:
  • Stored permanently in Cloudflare R2  (R2_DB_BUCKET/<slug>.db)
  • Cached locally in TENANT_DB_DIR/<slug>.db during the session
  • Synced back to R2 on request teardown if writes occurred

Performance:
  • In-process engine registry — avoids recreating SQLAlchemy engines per request
  • ETag-based freshness check — skips R2 download if local copy is current
  • WAL + optimised PRAGMAs — concurrent reads without full table locks
  • Dirty-set tracking — only syncs to R2 when a write actually happened
"""

import logging
import threading
from pathlib import Path
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from flask import current_app

from .r2_storage import download_tenant_db, upload_tenant_db, build_db_key
from models import TenantBase

logger = logging.getLogger(__name__)

# ── In-process engine registry (slug → Engine) ────────────────────────────────
_engine_registry: dict = {}
_registry_lock   = threading.Lock()

# ── Dirty-slug set: slugs that have unsynced writes ───────────────────────────
_dirty_slugs: set = set()
_dirty_lock  = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def get_tenant_engine(church_slug: str):
    """
    Return (and cache) a SQLAlchemy Engine for the given church slug.
    Downloads the .db from R2 on first access or after cache invalidation.
    """
    with _registry_lock:
        if church_slug not in _engine_registry:
            _engine_registry[church_slug] = _bootstrap_engine(church_slug)
        return _engine_registry[church_slug]


@contextmanager
def tenant_session(church_slug: str):
    """
    Context manager: yields a SQLAlchemy Session bound to the tenant's DB.

    • Commits automatically on clean exit.
    • Rolls back on exception.
    • Marks the slug dirty after any write so R2 sync is triggered.

    Usage:
        with tenant_session("cop-agona-ahanta") as session:
            posts = session.query(Post).all()
    """
    engine      = get_tenant_engine(church_slug)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session: Session = SessionLocal()

    try:
        yield session
        session.commit()
        # Mark dirty only if something was actually written
        if session.dirty or session.new or session.deleted:
            _mark_dirty(church_slug)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_dirty_tenants() -> int:
    """
    Push all locally-modified tenant DBs back to R2.
    Called by the teardown hook after each request, or manually via CLI.

    Returns:
        Number of DBs successfully synced.
    """
    with _dirty_lock:
        slugs = list(_dirty_slugs)
        _dirty_slugs.clear()

    synced = 0
    for slug in slugs:
        try:
            db_path = _tenant_db_dir() / f"{slug}.db"
            upload_tenant_db(build_db_key(slug), db_path)
            synced += 1
            logger.info("Tenant DB '%s' synced → R2.", slug)
        except Exception as exc:
            logger.error("Sync failed for tenant '%s': %s", slug, exc)
            _mark_dirty(slug)   # re-queue for next cycle

    return synced


def ensure_tenant_db_schema(church_slug: str) -> None:
    """
    Create all tenant tables in a (possibly fresh) database.
    Safe to call multiple times — uses checkfirst=True.
    """
    engine = get_tenant_engine(church_slug)
    TenantBase.metadata.create_all(engine, checkfirst=True)
    logger.info("Tenant schema ensured for '%s'.", church_slug)


def invalidate_tenant_cache(church_slug: str) -> None:
    """
    Evict a tenant engine from the in-process cache.
    Forces a fresh R2 download on the next request (e.g. after a DB restore).
    """
    with _registry_lock:
        engine = _engine_registry.pop(church_slug, None)
        if engine:
            engine.dispose()
            logger.info("Engine cache invalidated for tenant '%s'.", church_slug)


# ─────────────────────────────────────────────────────────────────────────────
# FLASK HOOKS  (register in the app factory)
# ─────────────────────────────────────────────────────────────────────────────

def register_db_sync_hooks(app) -> None:
    """
    Attach a teardown hook so dirty tenant DBs are synced to R2 at the
    end of each request.

    For high-traffic production use, replace with a Celery periodic task
    that calls sync_dirty_tenants() in the background.
    """
    @app.teardown_appcontext
    def _sync_tenant_dbs_on_teardown(exception):
        if exception is None and _dirty_slugs:
            sync_dirty_tenants()


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL
# ─────────────────────────────────────────────────────────────────────────────

def _bootstrap_engine(church_slug: str):
    """
    1. Resolve local DB path.
    2. Attempt to download from R2 if stale / absent.
    3. Create SQLAlchemy engine with optimised SQLite PRAGMAs.
    4. Run create_all() so schema exists even for a brand-new DB.
    """
    db_dir  = _tenant_db_dir()
    db_path = db_dir / f"{church_slug}.db"
    db_key  = build_db_key(church_slug)

    # ── Pull from R2 ──────────────────────────────────────────────────────────
    try:
        download_tenant_db(db_key, db_path)
    except Exception as exc:
        logger.warning(
            "Could not fetch tenant DB '%s' from R2 (%s). "
            "Using local copy if present, or creating fresh.",
            church_slug, exc,
        )

    # ── Create engine ─────────────────────────────────────────────────────────
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        echo=False,
    )

    # ── Per-connection SQLite PRAGMAs ─────────────────────────────────────────
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")        # concurrent reads + writes
        cursor.execute("PRAGMA synchronous=NORMAL;")      # safe + fast
        cursor.execute("PRAGMA cache_size=-65536;")       # 64 MB page cache
        cursor.execute("PRAGMA temp_store=MEMORY;")       # temp tables in RAM
        cursor.execute("PRAGMA foreign_keys=ON;")         # enforce FK constraints
        cursor.execute("PRAGMA busy_timeout=30000;")      # 30s lock timeout
        cursor.close()

    # ── Initialise schema ─────────────────────────────────────────────────────
    TenantBase.metadata.create_all(engine, checkfirst=True)

    logger.info("Tenant engine ready: '%s' @ %s", church_slug, db_path)
    return engine


def _tenant_db_dir() -> Path:
    path = Path(current_app.config["TENANT_DB_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _mark_dirty(slug: str) -> None:
    with _dirty_lock:
        _dirty_slugs.add(slug)
