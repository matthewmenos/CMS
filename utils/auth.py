"""
utils/auth.py — Authentication helpers for COP Agona Ahanta ChMS.

Provides:
  • JWT token generation and verification (flask-jwt-extended)
  • Flask-Login user loader
  • Role-based access decorators: @require_role, @require_admin
  • Tenant-aware current_member helper
"""

import logging
from functools import wraps
from typing import Optional

from flask import jsonify, request, current_app, g
from flask_login import current_user
from flask_jwt_extended import (
    JWTManager, create_access_token, get_jwt_identity, verify_jwt_in_request
)

from models import db, User
from .db_router import tenant_session

logger = logging.getLogger(__name__)

jwt = JWTManager()


# ─────────────────────────────────────────────────────────────────────────────
# JWT SETUP
# ─────────────────────────────────────────────────────────────────────────────

def init_jwt(app):
    """Initialise JWT extension with the Flask app."""
    jwt.init_app(app)

    @jwt.user_identity_loader
    def _user_identity(user: User) -> dict:
        """Encode user identity into the JWT payload."""
        return {"id": user.id, "email": user.email, "role": user.role}

    @jwt.user_lookup_loader
    def _user_lookup(_jwt_header, jwt_data) -> Optional[User]:
        """Reload the User from DB when a protected endpoint is accessed."""
        identity = jwt_data.get("sub", {})
        return User.query.get(identity.get("id"))

    @jwt.expired_token_loader
    def _expired(_header, _payload):
        return jsonify({"error": "Token has expired. Please log in again."}), 401

    @jwt.invalid_token_loader
    def _invalid(reason):
        return jsonify({"error": f"Invalid token: {reason}"}), 401

    @jwt.unauthorized_loader
    def _unauthorized(reason):
        return jsonify({"error": "Authentication required.", "detail": reason}), 401


def make_tokens(user: User) -> dict:
    """
    Create an access token (and optionally refresh token) for a User.

    Returns:
        {"access_token": "...", "token_type": "Bearer"}
    """
    access_token = create_access_token(identity=user)
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "user": user.to_dict(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# FLASK-LOGIN USER LOADER
# ─────────────────────────────────────────────────────────────────────────────

def register_login_manager(login_manager, app):
    """Attach the user loader to flask-login's LoginManager."""

    @login_manager.user_loader
    def load_user(user_id: str) -> Optional[User]:
        try:
            return User.query.get(int(user_id))
        except Exception:
            return None

    @login_manager.unauthorized_handler
    def _unauthorized():
        # API requests get JSON; browser requests get redirect
        if request.is_json or request.path.startswith("/api/"):
            return jsonify({"error": "Login required."}), 401
        from flask import redirect, url_for
        return redirect(url_for("auth.login"))


# ─────────────────────────────────────────────────────────────────────────────
# ROLE-BASED DECORATORS
# ─────────────────────────────────────────────────────────────────────────────

def require_role(*roles: str):
    """
    Decorator: enforce that the JWT user has one of the given roles.

    Usage:
        @app.route('/admin/stats')
        @require_role('admin', 'superadmin')
        def stats(): ...
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                verify_jwt_in_request()
            except Exception as exc:
                return jsonify({"error": "Authentication required.", "detail": str(exc)}), 401

            from flask_jwt_extended import get_jwt
            claims = get_jwt()
            identity = claims.get("sub", {})
            user_role = identity.get("role", "member")

            if user_role not in roles:
                return jsonify({
                    "error": "Permission denied.",
                    "required_roles": list(roles),
                    "your_role": user_role,
                }), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_admin(fn):
    """Shortcut: require 'admin' or 'superadmin' role."""
    return require_role("admin", "superadmin", "pastor")(fn)


# ─────────────────────────────────────────────────────────────────────────────
# TENANT-AWARE CURRENT MEMBER
# ─────────────────────────────────────────────────────────────────────────────

def get_current_member(church_slug: str):
    """
    Resolve the currently authenticated global User to their tenant Member record.
    Caches the result in Flask's `g` object for the request lifetime.

    Args:
        church_slug: The tenant slug extracted from the request.

    Returns:
        Member instance or None.
    """
    cache_key = f"_member_{church_slug}"
    if hasattr(g, cache_key):
        return getattr(g, cache_key)

    try:
        verify_jwt_in_request()
        from flask_jwt_extended import get_jwt
        identity = get_jwt().get("sub", {})
        user_id  = identity.get("id")
    except Exception:
        return None

    from models import Member
    with tenant_session(church_slug) as session:
        member = (
            session.query(Member)
            .filter_by(global_user_id=user_id)
            .first()
        )
        # Detach from session so it's safe to use outside the context
        if member:
            session.expunge(member)

    setattr(g, cache_key, member)
    return member
