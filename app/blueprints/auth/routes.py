"""
Auth blueprint: login, signup, logout.
Handles both HTML form submissions and JSON requests (for the SPA).
"""

import re
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify, current_app,
)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.exceptions import BadRequest

from app.models import db, GlobalUser, Church

auth_bp = Blueprint("auth", __name__, template_folder="../../templates/auth")

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._]{3,30}$")
_EMAIL_RE    = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _json_ok(data: dict, status: int = 200):
    return jsonify({"ok": True,  **data}), status


def _json_err(msg: str, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status


def _wants_json() -> bool:
    return request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "GET":
        return render_template("auth/login.html")

    data = request.get_json(silent=True) or request.form
    identifier = (data.get("identifier") or "").strip().lower()
    password   = (data.get("password")   or "").strip()

    if not identifier or not password:
        if _wants_json():
            return _json_err("Email/username and password are required.")
        flash("Please fill in all fields.", "error")
        return render_template("auth/login.html"), 400

    user = (
        GlobalUser.query.filter_by(email=identifier).first()
        or GlobalUser.query.filter_by(username=identifier).first()
    )

    if user is None or not user.check_password(password):
        if _wants_json():
            return _json_err("Invalid credentials.")
        flash("Invalid email/username or password.", "error")
        return render_template("auth/login.html"), 401

    if not user.is_active:
        if _wants_json():
            return _json_err("Account suspended. Contact your church admin.")
        flash("Your account has been suspended.", "error")
        return render_template("auth/login.html"), 403

    login_user(user, remember=True)
    user.last_login = datetime.now(timezone.utc)
    db.session.commit()

    if _wants_json():
        return _json_ok({
            "user": {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "church_slug": user.church.slug,
            }
        })

    return redirect(url_for("main.index"))


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------

@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "GET":
        return render_template("auth/signup.html")

    data     = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    email    = (data.get("email")    or "").strip().lower()
    password = (data.get("password") or "").strip()
    confirm  = (data.get("confirm")  or "").strip()

    # Validation
    if not all([username, email, password, confirm]):
        err = "All fields are required."
        return (_json_err(err) if _wants_json()
                else (flash(err, "error"), render_template("auth/signup.html"))[1])

    if not _USERNAME_RE.match(username):
        err = "Username must be 3-30 characters (letters, numbers, . and _ only)."
        return (_json_err(err) if _wants_json()
                else (flash(err, "error"), render_template("auth/signup.html"))[1])

    if not _EMAIL_RE.match(email):
        err = "Invalid email address."
        return (_json_err(err) if _wants_json()
                else (flash(err, "error"), render_template("auth/signup.html"))[1])

    if len(password) < 8:
        err = "Password must be at least 8 characters."
        return (_json_err(err) if _wants_json()
                else (flash(err, "error"), render_template("auth/signup.html"))[1])

    if password != confirm:
        err = "Passwords do not match."
        return (_json_err(err) if _wants_json()
                else (flash(err, "error"), render_template("auth/signup.html"))[1])

    if GlobalUser.query.filter_by(username=username).first():
        err = "That username is already taken."
        return (_json_err(err, 409) if _wants_json()
                else (flash(err, "error"), render_template("auth/signup.html"))[1])

    if GlobalUser.query.filter_by(email=email).first():
        err = "An account with that email already exists."
        return (_json_err(err, 409) if _wants_json()
                else (flash(err, "error"), render_template("auth/signup.html"))[1])

    slug   = current_app.config["CHURCH_SLUG"]
    church = Church.query.filter_by(slug=slug).first()
    if church is None:
        err = "Church not configured. Contact admin."
        return (_json_err(err, 500) if _wants_json()
                else (flash(err, "error"), render_template("auth/signup.html"))[1])

    user = GlobalUser(
        church_id=church.id,
        username=username,
        email=email,
        role="member",
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    # Create tenant member profile
    from app.utils.tenant import open_tenant_db, get_or_create_member, get_db
    from flask import g
    open_tenant_db(slug)
    get_or_create_member(get_db(), user.id, username)

    login_user(user, remember=True)

    if _wants_json():
        return _json_ok({"message": "Account created."}, 201)

    flash("Welcome to COP Agona Ahanta!", "success")
    return redirect(url_for("main.index"))


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@auth_bp.route("/logout", methods=["POST", "GET"])
@login_required
def logout():
    logout_user()
    if _wants_json():
        return _json_ok({"message": "Signed out."})
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Current user info (for SPA boot) — global DB only, no tenant DB dependency
# ---------------------------------------------------------------------------

@auth_bp.route("/me")
@login_required
def me():
    slug = current_app.config["CHURCH_SLUG"]
    # Return lightweight info from global DB only; tenant profile loaded lazily by the SPA
    return _json_ok({
        "user": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "display_name": current_user.username,
            "avatar_url": "",
            "church_slug": slug,
            "church_name": current_app.config["CHURCH_NAME"],
        }
    })
