"""blueprints/app/routes.py — HTML page routes (feed, reels, give, profile, search)."""

from flask import Blueprint, render_template, redirect, url_for, current_app
from flask_login import login_required, current_user

app_bp = Blueprint("app", __name__, template_folder="../../templates/app")


@app_bp.route("/")
def index():
    return redirect(url_for("app.feed"))


@app_bp.route("/feed")
@login_required
def feed():
    church_slug = current_app.config["CHURCH_SLUG"]
    return render_template("app/feed.html",
                           church_slug=church_slug,
                           church_name=current_app.config["CHURCH_NAME"])


@app_bp.route("/search")
@login_required
def search():
    return render_template("app/search.html")


@app_bp.route("/reels")
@login_required
def reels():
    return render_template("app/reels.html")


@app_bp.route("/give")
@login_required
def give():
    return render_template("app/give.html")


@app_bp.route("/profile")
@app_bp.route("/profile/<username>")
@login_required
def profile(username=None):
    return render_template("app/profile.html", username=username or current_user.username)
