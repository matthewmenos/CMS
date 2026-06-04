"""
Tenant database resolver / routing middleware.

Flow per request that needs tenant data:
  1. Read church_slug from the authenticated user's session.
  2. Look up the Church record in the global DB to get its db_key and local path.
  3. If the local .db file is missing, pull it from R2.
  4. Open a WAL connection and attach it to Flask's g object.
  5. After the response is sent, sync the .db back to R2 asynchronously (write ops only).
"""

import os
import time
import hashlib
import logging
from functools import wraps

from flask import g, abort, current_app
from flask_login import current_user

from app.models import get_tenant_conn, init_tenant_db, Church, db as global_db
from app.utils.r2 import download_tenant_db, upload_tenant_db_async

log = logging.getLogger(__name__)

_CACHE_TTL = 300   # seconds before re-checking R2 for a newer DB version
_sync_timestamps: dict[str, float] = {}


def _local_db_path(slug: str) -> str:
    base = current_app.config.get("TENANT_DB_DIR", "instance/tenants")
    return os.path.join(base, f"{slug}.db")


def _needs_refresh(slug: str) -> bool:
    last = _sync_timestamps.get(slug, 0)
    return (time.time() - last) > _CACHE_TTL


def open_tenant_db(slug: str) -> None:
    """
    Resolve, cache-check, and open the tenant DB.
    Attaches a sqlite3 connection to g.tenant_conn.
    Also attaches g.church_slug and g.db_dirty flag.
    """
    church: Church = (
        global_db.session.query(Church)
        .filter_by(slug=slug, is_active=True)
        .first()
    )
    if church is None:
        abort(404, description=f"Church '{slug}' not found.")

    local_path = _local_db_path(slug)
    db_key = church.db_key

    # Download from R2 if local file absent or cache expired
    if not os.path.exists(local_path) or _needs_refresh(slug):
        pulled = download_tenant_db(db_key, local_path)
        if not pulled and not os.path.exists(local_path):
            # Brand-new tenant — initialise empty DB
            init_tenant_db(local_path)
            log.info("Initialised new tenant DB for %s", slug)
        _sync_timestamps[slug] = time.time()

    g.tenant_conn   = get_tenant_conn(local_path)
    g.church_slug   = slug
    g.church        = church
    g.db_key        = db_key
    g.local_db_path = local_path
    g.db_dirty      = False


def close_tenant_db(exception=None) -> None:
    """Close connection; if writes happened, sync to R2 asynchronously."""
    conn = g.pop("tenant_conn", None)
    if conn is not None:
        conn.close()

    if g.pop("db_dirty", False):
        slug     = g.get("church_slug", "unknown")
        db_key   = g.get("db_key", "")
        local    = g.get("local_db_path", "")
        if db_key and local:
            upload_tenant_db_async(db_key, local)
            log.debug("Queued async DB sync for %s", slug)


def get_db():
    """Return the active tenant sqlite3 connection for the current request."""
    conn = getattr(g, "tenant_conn", None)
    if conn is None:
        raise RuntimeError("No tenant DB open — call open_tenant_db() first.")
    return conn


def mark_dirty():
    """Call after any INSERT / UPDATE / DELETE to trigger post-request R2 sync."""
    g.db_dirty = True


def require_tenant(f):
    """Decorator: open the tenant DB for the current user before the view runs."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        slug = current_app.config.get("CHURCH_SLUG", "cop-agona-ahanta")
        open_tenant_db(slug)
        return f(*args, **kwargs)
    return decorated


def get_or_create_member(conn, global_user_id: int, display_name: str) -> dict:
    """
    Ensure a Member row exists in the tenant DB for this global user.
    Returns the member row as a dict.
    """
    row = conn.execute(
        "SELECT * FROM members WHERE global_user_id = ?", (global_user_id,)
    ).fetchone()

    if row is None:
        conn.execute(
            """INSERT INTO members (global_user_id, display_name)
               VALUES (?, ?)""",
            (global_user_id, display_name),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM members WHERE global_user_id = ?", (global_user_id,)
        ).fetchone()
        mark_dirty()

    return dict(row)
