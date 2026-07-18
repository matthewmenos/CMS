"""
Main blueprint — serves the single-page shell for all UI routes.
"""

from flask import Blueprint, render_template
from flask_login import login_required

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@login_required
def index():
    return render_template("index.html")


@main_bp.route("/explore")
@login_required
def explore():
    return render_template("index.html")


@main_bp.route("/reels")
@login_required
def reels():
    return render_template("reels.html")


@main_bp.route("/give")
@login_required
def give():
    return render_template("index.html")


@main_bp.route("/profile")
@login_required
def profile():
    return render_template("index.html")


@main_bp.route("/stories")
@login_required
def stories():
    return render_template("stories.html")


@main_bp.route("/messages")
@login_required
def messages():
    return render_template("messages.html")


@main_bp.route("/create")
@login_required
def create_post():
    return render_template("create_post.html")


@main_bp.route("/create/story")
@login_required
def create_story():
    return render_template("create_story.html")


@main_bp.route("/praise")
@login_required
def praise():
    return render_template("praise.html")