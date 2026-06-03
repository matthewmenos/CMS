"""blueprints/auth/routes.py — Login, signup, logout endpoints."""

from flask import Blueprint, request, jsonify, render_template, redirect, url_for
from flask_login import login_user, logout_user, login_required
from models import db, User
from utils.auth import make_tokens

auth_bp = Blueprint("auth", __name__, template_folder="../../templates/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth/login.html")

    data = request.get_json(silent=True) or request.form
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = User.query.filter_by(email=email, is_active=True).first()
    if not user or not user.check_password(password):
        if request.is_json:
            return jsonify({"error": "Invalid email or password."}), 401
        return render_template("auth/login.html", error="Invalid email or password.")

    login_user(user, remember=True)
    tokens = make_tokens(user)

    if request.is_json:
        return jsonify(tokens), 200

    # Browser flow: set token in cookie and redirect to feed
    response = redirect(url_for("app.feed"))
    response.set_cookie(
        "access_token", tokens["access_token"],
        httponly=True, samesite="Lax", max_age=86400
    )
    return response


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("auth/signup.html")

    data     = request.get_json(silent=True) or request.form
    email    = (data.get("email") or "").strip().lower()
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    name     = (data.get("display_name") or data.get("name") or "").strip()

    # Basic validation
    errors = {}
    if not email:    errors["email"]    = "Email is required."
    if not username: errors["username"] = "Username is required."
    if len(password) < 8: errors["password"] = "Password must be at least 8 characters."
    if User.query.filter_by(email=email).first():
        errors["email"] = "Email already registered."
    if User.query.filter_by(username=username).first():
        errors["username"] = "Username already taken."

    if errors:
        if request.is_json:
            return jsonify({"errors": errors}), 422
        return render_template("auth/signup.html", errors=errors, form=data)

    user = User(email=email, username=username, display_name=name or username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    login_user(user)
    tokens = make_tokens(user)

    if request.is_json:
        return jsonify(tokens), 201

    response = redirect(url_for("app.feed"))
    response.set_cookie("access_token", tokens["access_token"],
                        httponly=True, samesite="Lax", max_age=86400)
    return response


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    response = redirect(url_for("auth.login"))
    response.delete_cookie("access_token")
    return response
