"""
Tenant database resolver.

Per-request flow:
  1. Resolve church slug → look up Church row in global DB.
  2. If the local .db file is absent or cache-expired → try R2 download
     (falls back silently to a fresh local init on any R2 failure).
  3. Open a WAL-mode sqlite3 connection and attach it to Flask g.
  4. After the response, teardown closes the connection; if writes happened,
     sync the .db back to R2 asynchronously.

Connection idempotence: calling open_tenant_db() twice on the same request
reuses the existing connection (no leak).
"""

import os
import time
import logging
import sqlite3
from functools import wraps

from flask import g, abort, current_app, has_request_context
from flask_login import current_user

from app.models import get_tenant_conn, init_tenant_db, Church, db as global_db
from app.utils.r2 import download_tenant_db, upload_tenant_db_async

log = logging.getLogger(__name__)

# Seconds before a locally-cached .db is considered stale and re-checked on R2.
_CACHE_TTL: int = 300
_sync_timestamps: dict[str, float] = {}


def _local_db_path(slug: str) -> str:
    base = current_app.config.get("TENANT_DB_DIR", "instance/tenants")
    return os.path.join(base, f"{slug}.db")


def _needs_r2_refresh(slug: str) -> bool:
    return (time.monotonic() - _sync_timestamps.get(slug, 0)) > _CACHE_TTL


def open_tenant_db(slug: str) -> None:
    """
    Open the tenant DB for *slug* and attach it to Flask's request globals (g).

    Idempotent — if a connection is already attached for this request the
    function returns immediately without opening a second one.
    """
    # Already open for this request → reuse
    if getattr(g, "tenant_conn", None) is not None:
        return

    church: Church = (
        global_db.session.query(Church)
        .filter_by(slug=slug, is_active=True)
        .first()
    )
    if church is None:
        abort(404, description=f"Church '{slug}' not found.")

    local_path = _local_db_path(slug)
    db_key     = church.db_key

    # Fetch from R2 only when the local copy is missing.
    # Never overwrite a local DB that has data (prevents data loss on new installs)
    if not os.path.exists(local_path):
        pulled = download_tenant_db(db_key, local_path)  # never raises
        if not pulled:
            init_tenant_db(local_path)
            log.info("Initialised new tenant DB for '%s'", slug)
    _sync_timestamps[slug] = time.monotonic()

    g.tenant_conn   = get_tenant_conn(local_path)
    g.church_slug   = slug
    g.church        = church
    g.db_key        = db_key
    g.local_db_path = local_path
    g.db_dirty      = False


def close_tenant_db(exception=None) -> None:
    """
    Teardown hook registered with app.teardown_appcontext.

    Closes the sqlite3 connection and, if writes occurred, queues an async
    sync back to R2.  Safe to call outside a request context.
    """
    # g is available inside app-context teardown but .pop raises RuntimeError
    # when there is no active app context at all — guard defensively.
    try:
        conn = g.pop("tenant_conn", None)
    except RuntimeError:
        return

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    try:
        dirty = g.pop("db_dirty", False)
    except RuntimeError:
        return

    if dirty:
        try:
            db_key = g.get("db_key", "")
            local  = g.get("local_db_path", "")
            slug   = g.get("church_slug", "unknown")
        except RuntimeError:
            return
        if db_key and local:
            upload_tenant_db_async(db_key, local)
            log.debug("Queued async DB sync for '%s'", slug)


def get_db():
    """Return the active tenant connection. Raises RuntimeError if none is open."""
    conn = getattr(g, "tenant_conn", None)
    if conn is None:
        raise RuntimeError(
            "No tenant DB open for this request — call open_tenant_db() first."
        )
    return conn


def mark_dirty() -> None:
    """Signal that writes occurred so the post-request R2 sync fires."""
    if has_request_context():
        g.db_dirty = True


def require_tenant(f):
    """
    View decorator: open the tenant DB before the view runs.
    Returns 401 if the user is not authenticated.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        open_tenant_db(current_app.config.get("CHURCH_SLUG", "cop-agona-ahanta"))
        return f(*args, **kwargs)
    return decorated


def get_or_create_member(conn, global_user_id: int, display_name: str) -> dict:
    """
    Ensure a Member row exists for *global_user_id* in the tenant DB.
    Returns the member row as a plain dict.
    """
    row = conn.execute(
        "SELECT * FROM members WHERE global_user_id = ?",
        (global_user_id,),
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO members (global_user_id, display_name) VALUES (?, ?)",
            (global_user_id, display_name),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM members WHERE global_user_id = ?",
            (global_user_id,),
        ).fetchone()
        # Only mark dirty if we are inside a request context
        mark_dirty()

    return dict(row)
