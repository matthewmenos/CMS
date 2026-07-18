"""
Audit logging for admin actions.
Records all administrative actions to the tenant database for accountability.
"""

import logging
from datetime import datetime, timezone
from flask import current_app, has_request_context
from flask_login import current_user

log = logging.getLogger(__name__)


def log_admin_action(action: str, details: dict = None) -> None:
    """
    Log an admin action to the tenant database.
    
    Args:
        action: The action type (e.g., 'user_suspend', 'post_delete', 'role_change')
        details: Optional dict with additional context
    """
    if not has_request_context():
        return
    
    try:
        from app.utils.tenant import get_db, mark_dirty
        
        conn = get_db()
        user_id = getattr(current_user, 'id', None)
        
        conn.execute(
            """INSERT INTO admin_audit (user_id, action, details, created_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, action, str(details or {}), 
             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        )
        conn.commit()
        mark_dirty()
    except Exception as e:
        log.warning("Could not log admin action: %s", e)


def get_audit_log(limit: int = 100):
    """
    Retrieve recent audit log entries.
    
    Args:
        limit: Maximum number of entries to return
    
    Returns:
        List of audit log entries
    """
    try:
        from app.utils.tenant import get_db
        from app.models import GlobalUser
        
        conn = get_db()
        rows = conn.execute(
            """SELECT a.*, u.username 
               FROM admin_audit a
               LEFT JOIN global_users u ON u.id = a.user_id
               ORDER BY a.id DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("Could not retrieve audit log: %s", e)
        return []