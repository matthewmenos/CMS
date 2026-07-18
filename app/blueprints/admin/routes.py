"""
Admin blueprint — server-rendered dashboard for church administrators.
Accessible only to users with role='admin'.
"""

from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, abort
from flask_login import login_required, current_user

from app.models import db, GlobalUser, Church
from app.utils.tenant import open_tenant_db, get_db, mark_dirty
from app.utils.audit import log_admin_action
from flask import current_app

admin_bp = Blueprint("admin", __name__, template_folder="../../templates/admin")


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role not in ("admin", "moderator"):
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _open_tenant():
    slug = current_app.config["CHURCH_SLUG"]
    open_tenant_db(slug)
    return get_db()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@admin_bp.get("/")
@admin_required
def dashboard():
    conn = _open_tenant()

    stats = {
        "members":  conn.execute("SELECT COUNT(*) FROM members").fetchone()[0],
        "posts":    conn.execute("SELECT COUNT(*) FROM posts    WHERE is_deleted=0").fetchone()[0],
        "stories":  conn.execute("SELECT COUNT(*) FROM stories  WHERE is_deleted=0").fetchone()[0],
        "comments": conn.execute("SELECT COUNT(*) FROM comments WHERE is_deleted=0").fetchone()[0],
        "giving_total": conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM giving WHERE status='confirmed'"
        ).fetchone()[0],
        "global_users": GlobalUser.query.count(),
    }

    recent_posts = conn.execute(
        """SELECT p.id, p.caption, p.like_count, p.comment_count, p.created_at,
                  m.display_name
           FROM posts p JOIN members m ON m.id=p.member_id
           WHERE p.is_deleted=0 ORDER BY p.id DESC LIMIT 10"""
    ).fetchall()

    return render_template(
        "admin/dashboard.html",
        stats=stats,
        recent_posts=[dict(r) for r in recent_posts],
        church_name=current_app.config["CHURCH_NAME"],
    )


# ---------------------------------------------------------------------------
# Members management
# ---------------------------------------------------------------------------

@admin_bp.get("/members")
@admin_required
def members():
    users = GlobalUser.query.order_by(GlobalUser.created_at.desc()).all()
    return render_template("admin/members.html", users=users,
                           church_name=current_app.config["CHURCH_NAME"])


@admin_bp.post("/members/<int:user_id>/toggle")
@admin_required
def toggle_member(user_id: int):
    user = GlobalUser.query.get_or_404(user_id)
    if user.role == "admin":
        flash("Cannot deactivate an admin account.", "error")
        return redirect(url_for("admin.members"))
    user.is_active = not user.is_active
    db.session.commit()
    log_admin_action("user_toggle", {"user_id": user_id, "is_active": user.is_active})
    flash(f"{'Activated' if user.is_active else 'Suspended'} {user.username}.", "success")
    return redirect(url_for("admin.members"))


@admin_bp.post("/members/<int:user_id>/role")
@admin_required
def change_role(user_id: int):
    user = GlobalUser.query.get_or_404(user_id)
    new_role = request.form.get("role", "member")
    if new_role not in ("member", "moderator", "admin"):
        flash("Invalid role.", "error")
    else:
        user.role = new_role
        db.session.commit()
        log_admin_action("role_change", {"user_id": user_id, "new_role": new_role})
        flash(f"Role updated to {new_role}.", "success")
    return redirect(url_for("admin.members"))


# ---------------------------------------------------------------------------
# Content moderation
# ---------------------------------------------------------------------------

@admin_bp.get("/posts")
@admin_required
def posts():
    conn = _open_tenant()
    rows = conn.execute(
        """SELECT p.*, m.display_name
           FROM posts p JOIN members m ON m.id=p.member_id
           ORDER BY p.id DESC LIMIT 50"""
    ).fetchall()
    return render_template("admin/posts.html",
                           posts=[dict(r) for r in rows],
                           church_name=current_app.config["CHURCH_NAME"])


@admin_bp.post("/posts/<int:post_id>/delete")
@admin_required
def admin_delete_post(post_id: int):
    conn = _open_tenant()
    conn.execute("UPDATE posts SET is_deleted=1 WHERE id=?", (post_id,))
    conn.commit()
    mark_dirty()
    log_admin_action("post_delete", {"post_id": post_id})
    flash("Post removed.", "success")
    return redirect(url_for("admin.posts"))


# ---------------------------------------------------------------------------
# Giving records
# ---------------------------------------------------------------------------

@admin_bp.get("/giving")
@admin_required
def giving():
    conn = _open_tenant()
    rows = conn.execute(
        """SELECT g.*, m.display_name
           FROM giving g LEFT JOIN members m ON m.id=g.member_id
           ORDER BY g.id DESC LIMIT 100"""
    ).fetchall()
    return render_template("admin/giving.html",
                           records=[dict(r) for r in rows],
                           church_name=current_app.config["CHURCH_NAME"])


@admin_bp.post("/giving/<int:record_id>/confirm")
@admin_required
def confirm_giving(record_id: int):
    conn = _open_tenant()
    conn.execute("UPDATE giving SET status='confirmed' WHERE id=?", (record_id,))
    conn.commit()
    mark_dirty()
    log_admin_action("giving_confirm", {"record_id": record_id})
    flash("Giving record confirmed.", "success")
    return redirect(url_for("admin.giving"))


# ---------------------------------------------------------------------------
# Events management
# ---------------------------------------------------------------------------

@admin_bp.get("/events")
@admin_required
def events():
    conn = _open_tenant()
    rows = conn.execute("SELECT * FROM events ORDER BY starts_at DESC LIMIT 50").fetchall()
    return render_template("admin/events.html",
                           events=[dict(r) for r in rows],
                           church_name=current_app.config["CHURCH_NAME"])


@admin_bp.post("/events/create")
@admin_required
def create_event():
    conn   = _open_tenant()
    from app.utils.tenant import get_or_create_member
    member = get_or_create_member(conn, current_user.id, current_user.username)

    title       = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    location    = request.form.get("location", "").strip()
    starts_at   = request.form.get("starts_at", "").strip()
    ends_at     = request.form.get("ends_at", "").strip() or None

    if not title or not starts_at:
        flash("Title and start date are required.", "error")
        return redirect(url_for("admin.events"))

    cur = conn.execute(
        """INSERT INTO events (title, description, location, starts_at, ends_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (title, description, location, starts_at, ends_at, member["id"]),
    )
    conn.commit()
    mark_dirty()
    log_admin_action("event_create", {"event_id": cur.lastrowid, "title": title})
    flash("Event created.", "success")
    return redirect(url_for("admin.events"))
