"""
blueprints/auth/routes.py — Login, signup, logout.

Login flow:
  1. Browser POSTs JSON credentials to /auth/login.
  2. Flask validates, returns { access_token, user } as JSON.
  3. JS stores token in localStorage, stores user in localStorage.
  4. JS does window.location.href = '/feed'.
  5. Feed page loads freely (no @login_required on page routes).
  6. Feed's inline <script> reads localStorage token → sets APP_CONFIG.token.
  7. If token missing → redirect to /auth/login.
  8. If token present → feed JS calls API with Bearer header → works.

Flask-Login session is set alongside JWT so that /admin routes
(which use @login_required for the HTML dashboard) also work.
"""

from flask import (
    Blueprint, request, jsonify, render_template,
    redirect, url_for, make_response,
)
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User
from utils.auth import make_tokens

auth_bp = Blueprint("auth", __name__, template_folder="../../templates/auth")


# ── Already logged in? Skip auth pages ───────────────────────────────────────
@auth_bp.before_request
def _redirect_if_authed():
    """Send already-authenticated users straight to the feed."""
    if request.endpoint in ("auth.login", "auth.signup") and current_user.is_authenticated:
        return redirect(url_for("app.feed"))


# ── Login ─────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth/login.html")

    # Accept JSON (from fetch) or form data
    data     = request.get_json(silent=True) or request.form
    email    = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "")

    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    user = User.query.filter_by(email=email, is_active=True).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid email or password."}), 401

    # Set Flask-Login session (needed for /admin @login_required routes)
    login_user(user, remember=True)

    # Return JWT payload — frontend stores in localStorage
    tokens = make_tokens(user)
    return jsonify(tokens), 200


# ── Signup ────────────────────────────────────────────────────────────────────

@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("auth/signup.html")

    data     = request.get_json(silent=True) or request.form
    email    = (data.get("email") or "").strip().lower()
    username = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "")
    name     = (data.get("display_name") or data.get("name") or "").strip()

    errors = {}
    if not email:
        errors["email"] = "Email is required."
    if not username:
        errors["username"] = "Username is required."
    if len(password) < 8:
        errors["password"] = "Password must be at least 8 characters."

    # Check duplicates only if format is valid
    if email and not errors.get("email"):
        if User.query.filter_by(email=email).first():
            errors["email"] = "Email already registered."
    if username and not errors.get("username"):
        if User.query.filter_by(username=username).first():
            errors["username"] = "Username already taken."

    if errors:
        return jsonify({"errors": errors}), 422

    user = User(
        email=email,
        username=username,
        display_name=name or username,
        is_active=True,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    # Also create a Member profile in the tenant DB
    _create_tenant_member(user)

    login_user(user, remember=True)
    tokens = make_tokens(user)
    return jsonify(tokens), 201


# ── Logout ────────────────────────────────────────────────────────────────────

@auth_bp.route("/logout")
def logout():
    logout_user()
    # Return JSON so JS can clear localStorage, OR redirect for direct nav
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({"logged_out": True}), 200
    response = make_response(redirect(url_for("auth.login")))
    response.delete_cookie("session")
    return response


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_tenant_member(user: User) -> None:
    """
    Auto-create a Member record in the default church's tenant DB
    when a new user registers. Silently skips if tenant DB is unavailable.
    """
    from flask import current_app
    from utils.db_router import tenant_session
    from models import Member

    slug = current_app.config["CHURCH_SLUG"]
    try:
        with tenant_session(slug) as session:
            # Avoid duplicate if somehow called twice
            exists = session.query(Member).filter_by(
                global_user_id=user.id
            ).first()
            if not exists:
                member = Member(
                    global_user_id=user.id,
                    username=user.username,
                    display_name=user.display_name or user.username,
                    avatar_url=user.avatar_url,
                    church_role="member",
                )
                session.add(member)
    except Exception as exc:
        current_app.logger.warning(
            "Could not create tenant member for user %s: %s", user.id, exc
        )
