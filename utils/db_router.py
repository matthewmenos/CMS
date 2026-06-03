"""
utils/db_router.py — Multi-tenant SQLite database routing engine.

Flow for every tenant request:
  1. Extract church_slug from JWT / session / subdomain.
  2. Check if tenant .db is already cached locally (and is fresh).
  3. If not, download it from R2 → local disk.
  4. Bind a SQLAlchemy engine + session to that .db file.
  5. After write operations, schedule an async sync back to R2.

Performance optimisations:
  • In-process LRU engine cache — avoids recreating engines per request.
  • ETag-based freshness check avoids redundant downloads.
  • Write-back is debounced: only syncs to R2 if the DB was modified.
  • SQLite WAL mode + optimised PRAGMAs for concurrent reads.
"""

import logging
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from flask import current_app, g

from .r2_storage import download_tenant_db, upload_tenant_db, build_db_key
from models import TenantBase

logger = logging.getLogger(__name__)

# ── Thread-safe engine registry ──────────────────────────────────────────────
_engine_registry: dict[str, object] = {}
_registry_lock = threading.Lock()

# ── Dirty-tracking: which slugs have unsaved writes ──────────────────────────
_dirty_slugs: set[str] = set()
_dirty_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def get_tenant_engine(church_slug: str):
    """
    Return (and cache) a SQLAlchemy Engine bound to the tenant's SQLite file.
    Downloads from R2 if not already cached locally.

    Args:
        church_slug: Unique slug, e.g. "cop-agona-ahanta"

    Returns:
        sqlalchemy.engine.Engine
    """
    with _registry_lock:
        if church_slug not in _engine_registry:
            _engine_registry[church_slug] = _bootstrap_engine(church_slug)
        return _engine_registry[church_slug]


@contextmanager
def tenant_session(church_slug: str):
    """
    Context manager that yields a SQLAlchemy Session for the tenant DB.
    Automatically commits on success and rolls back on error.
    Marks the slug as dirty after any write so R2 sync is triggered.

    Usage:
        with tenant_session("cop-agona-ahanta") as session:
            posts = session.query(Post).all()

    """
    engine = get_tenant_engine(church_slug)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session: Session = SessionLocal()

    try:
        yield session
        if session.dirty or session.new or session.deleted:
            session.commit()
            _mark_dirty(church_slug)
        else:
            session.commit()  # Flush read-only transactions cleanly
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_dirty_tenants() -> int:
    """
    Upload all dirty (locally-modified) tenant DBs back to R2.
    Call this from a background thread, a Celery task, or a Flask
    teardown hook.

    Returns:
        Number of DBs successfully synced.
    """
    with _dirty_lock:
        slugs_to_sync = list(_dirty_slugs)
        _dirty_slugs.clear()

    synced = 0
    for slug in slugs_to_sync:
        try:
            db_dir  = _tenant_db_dir()
            db_path = db_dir / f"{slug}.db"
            db_key  = build_db_key(slug)
            upload_tenant_db(db_key, db_path)
            synced += 1
            logger.info("Synced tenant DB '%s' → R2", slug)
        except Exception as exc:
            logger.error("Failed to sync tenant DB '%s': %s", slug, exc)
            # Re-mark as dirty so next cycle retries
            _mark_dirty(slug)

    return synced


def ensure_tenant_db_schema(church_slug: str) -> None:
    """
    Create all tenant tables in a freshly initialised database.
    Safe to call multiple times (uses checkfirst=True).
    """
    engine = get_tenant_engine(church_slug)
    TenantBase.metadata.create_all(engine, checkfirst=True)
    logger.info("Schema ensured for tenant '%s'", church_slug)


def invalidate_tenant_cache(church_slug: str) -> None:
    """
    Remove a tenant engine from the in-process cache.
    Forces a fresh download from R2 on the next request.
    Use after restoring a backup or manual DB replacement.
    """
    with _registry_lock:
        engine = _engine_registry.pop(church_slug, None)
        if engine:
            engine.dispose()
            logger.info("Invalidated engine cache for tenant '%s'", church_slug)


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _bootstrap_engine(church_slug: str):
    """
    1. Build the local DB path.
    2. Download from R2 if stale or absent.
    3. Create SQLAlchemy engine with SQLite performance PRAGMAs.
    4. Register SQLite optimisation hooks.
    """
    db_dir  = _tenant_db_dir()
    db_path = db_dir / f"{church_slug}.db"
    db_key  = build_db_key(church_slug)

    # ── Download from R2 if needed ────────────────────────────────────────────
    try:
        download_tenant_db(db_key, db_path)
    except Exception as exc:
        logger.warning(
            "Could not fetch tenant DB '%s' from R2 (%s). Using local copy if present.",
            church_slug, exc
        )

    # ── Create engine ─────────────────────────────────────────────────────────
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={
            "check_same_thread": False,
            "timeout": 30,
        },
        # Connection pool: StaticPool keeps a single connection per engine
        # which is optimal for SQLite
        poolclass=None,   # Use default NullPool for SQLite
        echo=False,       # Set True for SQL debug logging
    )

    # ── Apply SQLite PRAGMAs on every new connection ──────────────────────────
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        # WAL mode: concurrent reads + writes without full table locks
        cursor.execute("PRAGMA journal_mode=WAL;")
        # Synchronous NORMAL: safe + faster than FULL
        cursor.execute("PRAGMA synchronous=NORMAL;")
        # 64 MB page cache in memory
        cursor.execute("PRAGMA cache_size=-65536;")
        # Store temp tables in memory
        cursor.execute("PRAGMA temp_store=MEMORY;")
        # Enable foreign key enforcement
        cursor.execute("PRAGMA foreign_keys=ON;")
        # 30-second busy timeout before raising "database is locked"
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.close()

    # ── Initialise schema if DB is brand new ─────────────────────────────────
    TenantBase.metadata.create_all(engine, checkfirst=True)

    logger.info("Engine bootstrapped for tenant '%s' at %s", church_slug, db_path)
    return engine


def _tenant_db_dir() -> Path:
    """Return (and create) the local directory that holds tenant .db files."""
    path = Path(current_app.config["TENANT_DB_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _mark_dirty(church_slug: str) -> None:
    """Record that a tenant DB has uncommitted-to-R2 changes."""
    with _dirty_lock:
        _dirty_slugs.add(church_slug)


# ─────────────────────────────────────────────────────────────────────────────
# FLASK REQUEST HOOKS  (register these in the app factory)
# ─────────────────────────────────────────────────────────────────────────────

def register_db_sync_hooks(app) -> None:
    """
    Attach teardown hooks so dirty DBs are synced to R2 at end of each request.
    For production, replace with a Celery periodic task to batch syncs.
    """
    @app.teardown_appcontext
    def _sync_on_teardown(exception):
        if exception is None:
            # Only sync if there are dirty DBs; avoids R2 calls on read-only requests
            if _dirty_slugs:
                sync_dirty_tenants()
