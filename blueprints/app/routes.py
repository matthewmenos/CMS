"""
blueprints/app/routes.py — HTML page routes.

Authentication strategy:
  These routes serve HTML shells freely — NO @login_required here.
  Each page's inline <script> checks localStorage for the JWT token and
  redirects to /auth/login if missing. This is correct for a JWT-first
  single-page-style app where Flask-Login session is NOT used for pages.

  @login_required is only used on the /auth/logout route (session cleanup).
"""

from flask import Blueprint, render_template, redirect, url_for, current_app

app_bp = Blueprint("app", __name__, template_folder="../../templates/app")


@app_bp.route("/")
def index():
    """Root → redirect to feed (JS will redirect to login if no token)."""
    return redirect(url_for("app.feed"))


@app_bp.route("/feed")
def feed():
    """Main Instagram-style feed. Auth enforced client-side via JWT."""
    return render_template(
        "app/feed.html",
        church_slug=current_app.config["CHURCH_SLUG"],
        church_name=current_app.config["CHURCH_NAME"],
    )


@app_bp.route("/search")
def search():
    return render_template("app/search.html")


@app_bp.route("/reels")
def reels():
    return render_template("app/reels.html")


@app_bp.route("/give")
def give():
    return render_template("app/give.html")


@app_bp.route("/profile")
@app_bp.route("/profile/<username>")
def profile(username=None):
    from flask_login import current_user
    resolved = username or (current_user.username if current_user.is_authenticated else "")
    return render_template("app/profile.html", username=resolved)
