"""
blueprints/api/routes.py — REST API endpoints for COP Agona Ahanta ChMS.

All endpoints are prefixed with /api/v1 (registered in app.py).
JWT authentication is required for all write endpoints.

Endpoints:
  POST   /api/v1/feed/posts                — Create post
  GET    /api/v1/feed/posts                — Paginated feed
  POST   /api/v1/feed/posts/<id>/like      — Toggle like
  POST   /api/v1/feed/posts/<id>/comments  — Add comment
  GET    /api/v1/feed/posts/<id>/comments  — Get comments
  GET    /api/v1/stories                   — Active stories
  GET    /api/v1/reels                     — Reels feed
  GET    /api/v1/devotional/today          — Today's devotional
  POST   /api/v1/giving/record             — Record a giving
  GET    /api/v1/giving/history            — Member giving history
  POST   /api/v1/upload/presign            — Get presigned PUT URL
  GET    /api/v1/notifications             — User notifications
  PATCH  /api/v1/notifications/read        — Mark as read
  GET    /api/v1/profile/<username>        — Member profile
  PATCH  /api/v1/profile                  — Update own profile
"""

import uuid
import logging
from datetime import datetime, timezone, timedelta

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt

from models import db, User, Notification
from utils.db_router import tenant_session
from utils.r2_storage import generate_presigned_put_url, build_media_key
from utils.auth import get_current_member

logger = logging.getLogger(__name__)
api_bp = Blueprint("api", __name__)

CHURCH_SLUG_HEADER = "X-Church-Slug"


def _slug() -> str:
    """Extract church slug from header, falling back to app default."""
    return (
        request.headers.get(CHURCH_SLUG_HEADER)
        or current_app.config["CHURCH_SLUG"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# FEED / POSTS
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/feed/posts", methods=["GET"])
@jwt_required()
def get_posts():
    """Paginated post feed. page=1, per_page=12."""
    slug     = _slug()
    page     = max(1, request.args.get("page", 1, type=int))
    per_page = min(50, request.args.get("per_page", 12, type=int))

    from models import Post
    with tenant_session(slug) as session:
        total   = session.query(Post).count()
        posts   = (
            session.query(Post)
            .order_by(Post.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        data = [p.to_dict() for p in posts]

    return jsonify({
        "posts":    data,
        "page":     page,
        "per_page": per_page,
        "total":    total,
        "has_next": (page * per_page) < total,
    })


@api_bp.route("/feed/posts", methods=["POST"])
@jwt_required()
def create_post():
    """Create a new feed post."""
    slug   = _slug()
    member = get_current_member(slug)
    if not member:
        return jsonify({"error": "Member profile not found for this church."}), 404

    body = request.get_json(silent=True) or {}
    media_url    = body.get("media_url")
    media_type   = body.get("media_type", "text")
    caption      = (body.get("caption") or "").strip()
    thumbnail_url = body.get("thumbnail_url")
    tags         = body.get("tags", "")

    if not caption and not media_url:
        return jsonify({"error": "Post must have a caption or media."}), 422

    from models import Post
    with tenant_session(slug) as session:
        post = Post(
            author_id=member.id,
            media_url=media_url,
            media_type=media_type,
            thumbnail_url=thumbnail_url,
            caption=caption,
            tags=tags if isinstance(tags, str) else ",".join(tags),
        )
        session.add(post)
        session.flush()
        post_dict = post.to_dict()

        # Increment member post count
        m = session.query(type(member)).get(member.id)
        if m:
            m.posts_count = (m.posts_count or 0) + 1

    return jsonify({"post": post_dict}), 201


@api_bp.route("/feed/posts/<int:post_id>/like", methods=["POST"])
@jwt_required()
def toggle_like(post_id: int):
    """Toggle like on a post. Returns liked=True/False and new count."""
    slug   = _slug()
    member = get_current_member(slug)
    if not member:
        return jsonify({"error": "Member not found."}), 404

    from models import Post, Like
    with tenant_session(slug) as session:
        post = session.query(Post).get(post_id)
        if not post:
            return jsonify({"error": "Post not found."}), 404

        existing = (
            session.query(Like)
            .filter_by(member_id=member.id, post_id=post_id)
            .first()
        )
        if existing:
            session.delete(existing)
            post.likes_count = max(0, (post.likes_count or 1) - 1)
            liked = False
        else:
            like = Like(member_id=member.id, post_id=post_id)
            session.add(like)
            post.likes_count = (post.likes_count or 0) + 1
            liked = True

        count = post.likes_count

    return jsonify({"liked": liked, "likes_count": count})


@api_bp.route("/feed/posts/<int:post_id>/comments", methods=["GET"])
@jwt_required()
def get_comments(post_id: int):
    slug = _slug()
    page = max(1, request.args.get("page", 1, type=int))

    from models import Comment
    with tenant_session(slug) as session:
        comments = (
            session.query(Comment)
            .filter_by(post_id=post_id, parent_id=None)
            .order_by(Comment.created_at.asc())
            .offset((page - 1) * 20)
            .limit(20)
            .all()
        )
        data = [c.to_dict() for c in comments]

    return jsonify({"comments": data, "page": page})


@api_bp.route("/feed/posts/<int:post_id>/comments", methods=["POST"])
@jwt_required()
def add_comment(post_id: int):
    slug   = _slug()
    member = get_current_member(slug)
    if not member:
        return jsonify({"error": "Member not found."}), 404

    body      = request.get_json(silent=True) or {}
    text      = (body.get("body") or "").strip()
    parent_id = body.get("parent_id")

    if not text:
        return jsonify({"error": "Comment body is required."}), 422

    from models import Post, Comment
    with tenant_session(slug) as session:
        post = session.query(Post).get(post_id)
        if not post:
            return jsonify({"error": "Post not found."}), 404

        comment = Comment(
            post_id=post_id,
            author_id=member.id,
            parent_id=parent_id,
            body=text,
        )
        session.add(comment)
        post.comments_count = (post.comments_count or 0) + 1
        session.flush()
        data = comment.to_dict()

    return jsonify({"comment": data}), 201


# ─────────────────────────────────────────────────────────────────────────────
# STORIES
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/stories", methods=["GET"])
@jwt_required()
def get_stories():
    """Return active (non-expired) stories grouped by member."""
    slug = _slug()
    now  = datetime.now(timezone.utc)

    from models import Story
    with tenant_session(slug) as session:
        stories = (
            session.query(Story)
            .filter(Story.expires_at > now)
            .order_by(Story.created_at.desc())
            .limit(50)
            .all()
        )
        data = [s.to_dict() for s in stories]

    return jsonify({"stories": data})


@api_bp.route("/stories", methods=["POST"])
@jwt_required()
def create_story():
    slug   = _slug()
    member = get_current_member(slug)
    if not member:
        return jsonify({"error": "Member not found."}), 404

    body       = request.get_json(silent=True) or {}
    media_url  = body.get("media_url")
    media_type = body.get("media_type", "image")
    caption    = (body.get("caption") or "").strip()
    bg_color   = body.get("bg_color")

    if not media_url:
        return jsonify({"error": "media_url is required."}), 422

    from models import Story
    now = datetime.now(timezone.utc)
    with tenant_session(slug) as session:
        story = Story(
            author_id=member.id,
            media_url=media_url,
            media_type=media_type,
            caption=caption,
            bg_color=bg_color,
            expires_at=now + timedelta(hours=24),
        )
        session.add(story)
        session.flush()
        data = story.to_dict()

    return jsonify({"story": data}), 201


# ─────────────────────────────────────────────────────────────────────────────
# REELS
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/reels", methods=["GET"])
@jwt_required()
def get_reels():
    slug     = _slug()
    page     = max(1, request.args.get("page", 1, type=int))
    per_page = min(20, request.args.get("per_page", 10, type=int))

    from models import Reel
    with tenant_session(slug) as session:
        reels = (
            session.query(Reel)
            .order_by(Reel.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        data = [r.to_dict() for r in reels]

    return jsonify({"reels": data, "page": page})


# ─────────────────────────────────────────────────────────────────────────────
# DEVOTIONAL
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/devotional/today", methods=["GET"])
@jwt_required()
def get_today_devotional():
    slug  = _slug()
    today = datetime.now(timezone.utc).date()

    from models import DevotionalDay
    with tenant_session(slug) as session:
        dev = session.query(DevotionalDay).filter_by(day_date=today).first()
        data = dev.to_dict() if dev else None

    return jsonify({"devotional": data})


# ─────────────────────────────────────────────────────────────────────────────
# GIVING
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/giving/record", methods=["POST"])
@jwt_required()
def record_giving():
    slug   = _slug()
    member = get_current_member(slug)
    if not member:
        return jsonify({"error": "Member not found."}), 404

    body   = request.get_json(silent=True) or {}
    amount = body.get("amount")
    if not amount or float(amount) <= 0:
        return jsonify({"error": "Valid amount is required."}), 422

    from models import GivingRecord
    with tenant_session(slug) as session:
        record = GivingRecord(
            member_id=member.id,
            amount=float(amount),
            currency=body.get("currency", "GHS"),
            giving_type=body.get("giving_type", "tithe"),
            reference=body.get("reference") or str(uuid.uuid4()),
            payment_method=body.get("payment_method"),
            is_anonymous=body.get("is_anonymous", False),
            notes=body.get("notes"),
        )
        session.add(record)
        session.flush()
        data = record.to_dict()

    return jsonify({"record": data}), 201


@api_bp.route("/giving/history", methods=["GET"])
@jwt_required()
def giving_history():
    slug   = _slug()
    member = get_current_member(slug)
    if not member:
        return jsonify({"error": "Member not found."}), 404

    page = max(1, request.args.get("page", 1, type=int))
    from models import GivingRecord
    with tenant_session(slug) as session:
        records = (
            session.query(GivingRecord)
            .filter_by(member_id=member.id)
            .order_by(GivingRecord.created_at.desc())
            .offset((page - 1) * 20)
            .limit(20)
            .all()
        )
        data = [r.to_dict() for r in records]

    return jsonify({"records": data, "page": page})


# ─────────────────────────────────────────────────────────────────────────────
# PRESIGNED UPLOAD URL
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/upload/presign", methods=["POST"])
@jwt_required()
def get_presign_url():
    """
    Returns a presigned PUT URL so the client can upload directly to R2.
    Body: { "content_type": "video/mp4", "media_category": "posts" }
    """
    body         = request.get_json(silent=True) or {}
    content_type = body.get("content_type", "application/octet-stream")
    category     = body.get("media_category", "posts")  # posts | reels | stories | avatars

    # Validate content type
    allowed = (
        current_app.config["ALLOWED_IMAGE_TYPES"]
        | current_app.config["ALLOWED_VIDEO_TYPES"]
        | current_app.config["ALLOWED_AUDIO_TYPES"]
    )
    if content_type not in allowed:
        return jsonify({"error": f"Content type '{content_type}' is not allowed."}), 422

    # Build a unique object key
    ext        = content_type.split("/")[-1].replace("jpeg", "jpg")
    unique_id  = uuid.uuid4().hex
    slug       = _slug()
    object_key = build_media_key(slug, category, f"{unique_id}.{ext}")

    url = generate_presigned_put_url(object_key, content_type)

    return jsonify({
        "upload_url": url,
        "object_key": object_key,
        "expires_in": current_app.config.get("R2_PRESIGN_EXPIRY", 3600),
    })


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/notifications", methods=["GET"])
@jwt_required()
def get_notifications():
    identity = get_jwt().get("sub", {})
    user_id  = identity.get("id")
    slug     = _slug()
    page     = max(1, request.args.get("page", 1, type=int))

    notifs = (
        Notification.query
        .filter_by(user_id=user_id, church_slug=slug)
        .order_by(Notification.created_at.desc())
        .offset((page - 1) * 20)
        .limit(20)
        .all()
    )
    unread = (
        Notification.query
        .filter_by(user_id=user_id, church_slug=slug, is_read=False)
        .count()
    )
    return jsonify({
        "notifications": [n.to_dict() for n in notifs],
        "unread_count": unread,
        "page": page,
    })


@api_bp.route("/notifications/read", methods=["PATCH"])
@jwt_required()
def mark_notifications_read():
    identity = get_jwt().get("sub", {})
    user_id  = identity.get("id")
    slug     = _slug()
    ids      = request.get_json(silent=True, force=True).get("ids") or []

    q = Notification.query.filter_by(user_id=user_id, church_slug=slug, is_read=False)
    if ids:
        q = q.filter(Notification.id.in_(ids))
    updated = q.update({"is_read": True}, synchronize_session=False)
    db.session.commit()

    return jsonify({"updated": updated})


# ─────────────────────────────────────────────────────────────────────────────
# PROFILE
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/profile/<username>", methods=["GET"])
@jwt_required()
def get_profile(username: str):
    slug = _slug()
    from models import Member
    with tenant_session(slug) as session:
        member = session.query(Member).filter_by(username=username).first()
        if not member:
            return jsonify({"error": "Member not found."}), 404
        data = member.to_dict()

    return jsonify({"member": data})


@api_bp.route("/profile", methods=["PATCH"])
@jwt_required()
def update_profile():
    slug   = _slug()
    member = get_current_member(slug)
    if not member:
        return jsonify({"error": "Member not found."}), 404

    body = request.get_json(silent=True) or {}
    allowed_fields = {"display_name", "bio", "avatar_url", "phone", "department"}

    from models import Member
    with tenant_session(slug) as session:
        m = session.query(Member).get(member.id)
        for field in allowed_fields:
            if field in body:
                setattr(m, field, body[field])
        session.flush()
        data = m.to_dict()

    return jsonify({"member": data})
