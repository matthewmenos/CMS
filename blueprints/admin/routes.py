"""
blueprints/admin/routes.py — Admin dashboard routes for COP Agona Ahanta ChMS.
"""

from flask import Blueprint, render_template, jsonify, request, current_app
from flask_login import login_required
from models import db, Notification
from utils.auth import require_admin
from utils.db_router import tenant_session, sync_dirty_tenants
from datetime import datetime

admin_bp = Blueprint("admin", __name__, template_folder="../../templates/admin")


@admin_bp.before_request
@login_required
def _require_login():
    pass


@admin_bp.route("/")
@require_admin
def dashboard():
    slug  = current_app.config["CHURCH_SLUG"]
    stats = _get_dashboard_stats(slug)
    return render_template("admin/dashboard.html", stats=stats,
                           church_name=current_app.config["CHURCH_NAME"])


@admin_bp.route("/members")
@require_admin
def members():
    slug = current_app.config["CHURCH_SLUG"]
    page = max(1, request.args.get("page", 1, type=int))
    from models import Member
    with tenant_session(slug) as session:
        total = session.query(Member).count()
        items = (session.query(Member).order_by(Member.created_at.desc())
                 .offset((page - 1) * 30).limit(30).all())
        data = [m.to_dict() for m in items]
    return jsonify({"members": data, "total": total, "page": page})


@admin_bp.route("/members/<int:member_id>/role", methods=["PATCH"])
@require_admin
def update_member_role(member_id):
    slug = current_app.config["CHURCH_SLUG"]
    body = request.get_json(silent=True) or {}
    role = body.get("church_role")
    valid = ("member","pastor","deacon","elder","admin","choir","youth_leader")
    if role not in valid:
        return jsonify({"error": "Invalid role."}), 422
    from models import Member
    with tenant_session(slug) as session:
        m = session.query(Member).get(member_id)
        if not m:
            return jsonify({"error": "Member not found."}), 404
        m.church_role = role
    return jsonify({"updated": True, "church_role": role})


@admin_bp.route("/posts", methods=["GET"])
@require_admin
def list_posts():
    slug = current_app.config["CHURCH_SLUG"]
    page = max(1, request.args.get("page", 1, type=int))
    from models import Post
    with tenant_session(slug) as session:
        total = session.query(Post).count()
        posts = (session.query(Post).order_by(Post.created_at.desc())
                 .offset((page - 1) * 20).limit(20).all())
        data = [p.to_dict() for p in posts]
    return jsonify({"posts": data, "total": total, "page": page})


@admin_bp.route("/posts/<int:post_id>", methods=["DELETE"])
@require_admin
def delete_post(post_id):
    slug = current_app.config["CHURCH_SLUG"]
    from models import Post
    with tenant_session(slug) as session:
        p = session.query(Post).get(post_id)
        if not p:
            return jsonify({"error": "Post not found."}), 404
        session.delete(p)
    return jsonify({"deleted": True})


@admin_bp.route("/posts/<int:post_id>/pin", methods=["PATCH"])
@require_admin
def pin_post(post_id):
    slug = current_app.config["CHURCH_SLUG"]
    from models import Post
    with tenant_session(slug) as session:
        p = session.query(Post).get(post_id)
        if not p:
            return jsonify({"error": "Post not found."}), 404
        p.is_pinned = not p.is_pinned
        pinned = p.is_pinned
    return jsonify({"is_pinned": pinned})


@admin_bp.route("/devotionals", methods=["GET"])
@require_admin
def list_devotionals():
    slug = current_app.config["CHURCH_SLUG"]
    from models import DevotionalDay
    with tenant_session(slug) as session:
        devs = (session.query(DevotionalDay)
                .order_by(DevotionalDay.day_date.desc()).limit(30).all())
        data = [d.to_dict() for d in devs]
    return jsonify({"devotionals": data})


@admin_bp.route("/devotionals", methods=["POST"])
@require_admin
def create_devotional():
    slug = current_app.config["CHURCH_SLUG"]
    body = request.get_json(silent=True) or {}
    for f in ("title", "reflection", "day_date"):
        if not body.get(f):
            return jsonify({"error": f"'{f}' is required."}), 422
    try:
        day_date = datetime.strptime(body["day_date"], "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "day_date must be YYYY-MM-DD."}), 422
    from models import DevotionalDay
    with tenant_session(slug) as session:
        if session.query(DevotionalDay).filter_by(day_date=day_date).first():
            return jsonify({"error": "Devotional for this date already exists."}), 409
        dev = DevotionalDay(title=body["title"], scripture=body.get("scripture"),
                            scripture_text=body.get("scripture_text"),
                            reflection=body["reflection"], prayer=body.get("prayer"),
                            cover_image_url=body.get("cover_image_url"), day_date=day_date)
        session.add(dev)
        session.flush()
        data = dev.to_dict()
    return jsonify({"devotional": data}), 201


@admin_bp.route("/giving/analytics", methods=["GET"])
@require_admin
def giving_analytics():
    slug = current_app.config["CHURCH_SLUG"]
    from models import GivingRecord
    from sqlalchemy import func
    with tenant_session(slug) as session:
        rows = (session.query(GivingRecord.giving_type,
                              func.sum(GivingRecord.amount).label("total"),
                              func.count(GivingRecord.id).label("count"))
                .group_by(GivingRecord.giving_type).all())
        data = [{"giving_type": t, "total": float(s or 0), "count": c} for t, s, c in rows]
    return jsonify({"analytics": data})


@admin_bp.route("/broadcast", methods=["POST"])
@require_admin
def broadcast():
    slug    = current_app.config["CHURCH_SLUG"]
    body    = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    link    = body.get("link")
    if not message:
        return jsonify({"error": "message is required."}), 422
    from models import Member
    with tenant_session(slug) as session:
        user_ids = [m.global_user_id for m in session.query(Member.global_user_id).all()]
    notifs = [Notification(user_id=uid, church_slug=slug, notif_type="broadcast",
                           message=message, link=link) for uid in user_ids]
    db.session.bulk_save_objects(notifs)
    db.session.commit()
    return jsonify({"sent": len(notifs)})


@admin_bp.route("/sync-db", methods=["POST"])
@require_admin
def manual_sync():
    n = sync_dirty_tenants()
    return jsonify({"synced": n})


def _get_dashboard_stats(slug):
    from models import Member, Post, GivingRecord
    from sqlalchemy import func
    try:
        with tenant_session(slug) as session:
            mc  = session.query(Member).count()
            pc  = session.query(Post).count()
            tg  = session.query(func.sum(GivingRecord.amount)).scalar() or 0
            rp  = session.query(Post).order_by(Post.created_at.desc()).limit(5).all()
            rpd = [p.to_dict() for p in rp]
    except Exception:
        mc, pc, tg, rpd = 0, 0, 0, []
    return {"member_count": mc, "post_count": pc, "total_giving": float(tg), "recent_posts": rpd}
