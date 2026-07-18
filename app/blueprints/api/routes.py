"""
API blueprint — all JSON endpoints consumed by the SPA.

Endpoints:
  Feed & Posts  : GET  /api/feed
                  POST /api/posts
                  GET  /api/posts/<id>
                  DELETE /api/posts/<id>
  Stories       : GET  /api/stories
                  POST /api/stories
  Likes         : POST /api/posts/<id>/like   (toggle)
  Comments      : GET  /api/posts/<id>/comments
                  POST /api/posts/<id>/comments
  Presigned URL : POST /api/upload/presign
  Profile       : GET  /api/profile/<username>
  Notifications : GET  /api/notifications
                  POST /api/notifications/read
  Giving        : POST /api/give
  Events        : GET  /api/events
"""

import os
import uuid
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify, request, current_app, abort
from flask_login import login_required, current_user

from app.utils.tenant import require_tenant, get_db, mark_dirty, get_or_create_member
from app.utils.r2 import (
    generate_upload_presigned_url,
    public_media_url,
    allowed_content_type,
    delete_object,
)
from app.utils.rate_limit import upload_rate_limit, giving_rate_limit

api_bp = Blueprint("api", __name__)

PAGE_SIZE = 12


def _ok(data=None, status=200, **kwargs):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(kwargs)
    return jsonify(payload), status


def _err(msg: str, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status


def _current_member(conn):
    return get_or_create_member(conn, current_user.id, current_user.username)


# ---------------------------------------------------------------------------
# PRESIGNED UPLOAD URL
# ---------------------------------------------------------------------------

@api_bp.post("/upload/presign")
@login_required
@require_tenant
@upload_rate_limit
def presign_upload():
    body         = request.get_json(silent=True) or {}
    content_type = body.get("content_type", "")
    filename     = body.get("filename", "file")

    if not allowed_content_type(content_type):
        return _err(f"Unsupported content type: {content_type}")

    ext      = content_type.split("/")[1].split(";")[0]
    slug     = current_app.config["CHURCH_SLUG"]
    obj_key  = f"media/{slug}/{uuid.uuid4().hex}.{ext}"

    url = generate_upload_presigned_url(obj_key, content_type)
    if url is None:
        return _err("Could not generate upload URL.")

    return _ok({"upload_url": url, "object_key": obj_key, "public_url": public_media_url(obj_key)})


# ---------------------------------------------------------------------------
# FEED
# ---------------------------------------------------------------------------

@api_bp.get("/feed")
@login_required
@require_tenant
def feed():
    conn   = get_db()
    member = _current_member(conn)
    cursor = int(request.args.get("cursor", 0))

    rows = conn.execute(
        """
        SELECT p.*, m.display_name, m.avatar_key, m.global_user_id,
               (SELECT 1 FROM likes l WHERE l.post_id=p.id AND l.member_id=?) as liked
        FROM posts p
        JOIN members m ON m.id = p.member_id
        WHERE p.is_deleted = 0
        ORDER BY p.id DESC
        LIMIT ? OFFSET ?
        """,
        (member["id"], PAGE_SIZE, cursor),
    ).fetchall()

    posts = []
    for r in rows:
        r = dict(r)
        r["media_url"]  = public_media_url(r["media_key"])
        r["avatar_url"] = public_media_url(r["avatar_key"]) if r["avatar_key"] else ""
        r.pop("avatar_key", None)
        posts.append(r)

    return _ok(posts, next_cursor=cursor + PAGE_SIZE if len(rows) == PAGE_SIZE else None)


# ---------------------------------------------------------------------------
# POSTS — create / read / delete
# ---------------------------------------------------------------------------

@api_bp.post("/posts")
@login_required
@require_tenant
def create_post():
    conn   = get_db()
    member = _current_member(conn)
    body   = request.get_json(silent=True) or {}

    media_key  = (body.get("media_key")  or "").strip()
    media_type = (body.get("media_type") or "image").strip()
    caption    = (body.get("caption")    or "").strip()[:2200]
    location   = (body.get("location")   or "").strip()[:100]

    if not media_key:
        return _err("media_key is required.")
    if media_type not in ("image", "video", "reel"):
        return _err("media_type must be image, video, or reel.")

    cur = conn.execute(
        """INSERT INTO posts (member_id, media_key, media_type, caption, location)
           VALUES (?, ?, ?, ?, ?)""",
        (member["id"], media_key, media_type, caption, location),
    )
    conn.execute(
        "UPDATE members SET post_count = post_count + 1 WHERE id = ?",
        (member["id"],),
    )
    conn.commit()
    mark_dirty()

    post = dict(conn.execute("SELECT * FROM posts WHERE id=?", (cur.lastrowid,)).fetchone())
    post["media_url"]    = public_media_url(post["media_key"])
    post["display_name"] = member["display_name"]
    post["avatar_url"]   = public_media_url(member["avatar_key"]) if member["avatar_key"] else ""
    return _ok(post, status=201)


@api_bp.get("/posts/<int:post_id>")
@login_required
@require_tenant
def get_post(post_id: int):
    conn   = get_db()
    member = _current_member(conn)
    row = conn.execute(
        """SELECT p.*, m.display_name, m.avatar_key,
                  (SELECT 1 FROM likes l WHERE l.post_id=p.id AND l.member_id=?) as liked
           FROM posts p JOIN members m ON m.id=p.member_id
           WHERE p.id=? AND p.is_deleted=0""",
        (member["id"], post_id),
    ).fetchone()

    if not row:
        return _err("Post not found.", 404)

    post = dict(row)
    post["media_url"]  = public_media_url(post["media_key"])
    post["avatar_url"] = public_media_url(post["avatar_key"]) if post["avatar_key"] else ""
    return _ok(post)


@api_bp.delete("/posts/<int:post_id>")
@login_required
@require_tenant
def delete_post(post_id: int):
    conn   = get_db()
    member = _current_member(conn)

    post = conn.execute("SELECT * FROM posts WHERE id=? AND is_deleted=0", (post_id,)).fetchone()
    if not post:
        return _err("Post not found.", 404)

    post = dict(post)
    if post["member_id"] != member["id"] and current_user.role not in ("admin", "moderator"):
        return _err("Forbidden.", 403)

    conn.execute("UPDATE posts SET is_deleted=1 WHERE id=?", (post_id,))
    conn.execute(
        "UPDATE members SET post_count = MAX(0, post_count-1) WHERE id=?",
        (member["id"],),
    )
    conn.commit()
    mark_dirty()
    return _ok({"deleted": True})


# ---------------------------------------------------------------------------
# STORIES
# ---------------------------------------------------------------------------

@api_bp.get("/stories")
@login_required
@require_tenant
def get_stories():
    conn = get_db()
    now  = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """SELECT s.*, m.display_name, m.avatar_key
           FROM stories s JOIN members m ON m.id=s.member_id
           WHERE s.is_deleted=0 AND s.expires_at > ?
           ORDER BY s.id DESC LIMIT 50""",
        (now,),
    ).fetchall()

    stories = []
    for r in rows:
        r = dict(r)
        r["media_url"]  = public_media_url(r["media_key"])
        r["avatar_url"] = public_media_url(r["avatar_key"]) if r["avatar_key"] else ""
        stories.append(r)

    return _ok(stories)


@api_bp.post("/stories")
@login_required
@require_tenant
def create_story():
    conn   = get_db()
    member = _current_member(conn)
    body   = request.get_json(silent=True) or {}

    media_key  = (body.get("media_key")  or "").strip()
    media_type = (body.get("media_type") or "image").strip()
    caption    = (body.get("caption")    or "").strip()[:300]

    if not media_key:
        return _err("media_key is required.")

    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cur = conn.execute(
        """INSERT INTO stories (member_id, media_key, media_type, caption, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (member["id"], media_key, media_type, caption, expires),
    )
    conn.commit()
    mark_dirty()
    return _ok({"id": cur.lastrowid, "expires_at": expires}, status=201)


# ---------------------------------------------------------------------------
# LIKES  (toggle)
# ---------------------------------------------------------------------------

@api_bp.post("/posts/<int:post_id>/like")
@login_required
@require_tenant
def toggle_like(post_id: int):
    conn   = get_db()
    member = _current_member(conn)

    existing = conn.execute(
        "SELECT id FROM likes WHERE post_id=? AND member_id=?",
        (post_id, member["id"]),
    ).fetchone()

    if existing:
        conn.execute("DELETE FROM likes WHERE post_id=? AND member_id=?", (post_id, member["id"]))
        conn.execute("UPDATE posts SET like_count=MAX(0,like_count-1) WHERE id=?", (post_id,))
        liked = False
    else:
        conn.execute(
            "INSERT OR IGNORE INTO likes (post_id, member_id) VALUES (?, ?)",
            (post_id, member["id"]),
        )
        conn.execute("UPDATE posts SET like_count=like_count+1 WHERE id=?", (post_id,))
        liked = True
        # Notification (best-effort)
        post_owner = conn.execute("SELECT member_id FROM posts WHERE id=?", (post_id,)).fetchone()
        if post_owner and post_owner["member_id"] != member["id"]:
            conn.execute(
                """INSERT INTO notifications (recipient_id, actor_id, type, post_id)
                   VALUES (?, ?, 'like', ?)""",
                (post_owner["member_id"], member["id"], post_id),
            )

    count = conn.execute("SELECT like_count FROM posts WHERE id=?", (post_id,)).fetchone()
    conn.commit()
    mark_dirty()
    return _ok({"liked": liked, "like_count": count["like_count"] if count else 0})


# ---------------------------------------------------------------------------
# COMMENTS
# ---------------------------------------------------------------------------

@api_bp.get("/posts/<int:post_id>/comments")
@login_required
@require_tenant
def get_comments(post_id: int):
    conn   = get_db()
    member = _current_member(conn)
    cursor = int(request.args.get("cursor", 0))

    rows = conn.execute(
        """SELECT c.*, m.display_name, m.avatar_key
           FROM comments c JOIN members m ON m.id=c.member_id
           WHERE c.post_id=? AND c.is_deleted=0 AND c.parent_id IS NULL
           ORDER BY c.id ASC LIMIT ? OFFSET ?""",
        (post_id, PAGE_SIZE, cursor),
    ).fetchall()

    comments = []
    for r in rows:
        r = dict(r)
        r["avatar_url"] = public_media_url(r["avatar_key"]) if r["avatar_key"] else ""
        comments.append(r)

    return _ok(comments, next_cursor=cursor + PAGE_SIZE if len(rows) == PAGE_SIZE else None)


@api_bp.post("/posts/<int:post_id>/comments")
@login_required
@require_tenant
def add_comment(post_id: int):
    conn   = get_db()
    member = _current_member(conn)
    body   = request.get_json(silent=True) or {}

    text      = (body.get("body") or "").strip()[:1000]
    parent_id = body.get("parent_id")

    if not text:
        return _err("Comment body is required.")

    cur = conn.execute(
        """INSERT INTO comments (post_id, member_id, parent_id, body)
           VALUES (?, ?, ?, ?)""",
        (post_id, member["id"], parent_id or None, text),
    )
    conn.execute("UPDATE posts SET comment_count=comment_count+1 WHERE id=?", (post_id,))

    post_owner = conn.execute("SELECT member_id FROM posts WHERE id=?", (post_id,)).fetchone()
    if post_owner and post_owner["member_id"] != member["id"]:
        conn.execute(
            """INSERT INTO notifications (recipient_id, actor_id, type, post_id, comment_id)
               VALUES (?, ?, 'comment', ?, ?)""",
            (post_owner["member_id"], member["id"], post_id, cur.lastrowid),
        )

    conn.commit()
    mark_dirty()

    comment = dict(conn.execute("SELECT * FROM comments WHERE id=?", (cur.lastrowid,)).fetchone())
    comment["display_name"] = member["display_name"]
    comment["avatar_url"]   = public_media_url(member["avatar_key"]) if member["avatar_key"] else ""
    return _ok(comment, status=201)


# ---------------------------------------------------------------------------
# PROFILE
# ---------------------------------------------------------------------------

@api_bp.get("/profile/<username>")
@login_required
@require_tenant
def get_profile(username: str):
    from app.models import GlobalUser
    user = GlobalUser.query.filter_by(username=username).first()
    if not user:
        return _err("User not found.", 404)

    conn    = get_db()
    viewer  = _current_member(conn)
    member  = conn.execute(
        "SELECT * FROM members WHERE global_user_id=?", (user.id,)
    ).fetchone()

    if not member:
        return _err("Profile not found.", 404)

    member = dict(member)
    is_following = bool(conn.execute(
        "SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?",
        (viewer["id"], member["id"]),
    ).fetchone())

    posts = conn.execute(
        """SELECT id, media_key, media_type, like_count, comment_count, created_at
           FROM posts WHERE member_id=? AND is_deleted=0
           ORDER BY id DESC LIMIT 30""",
        (member["id"],),
    ).fetchall()

    post_list = []
    for p in posts:
        p = dict(p)
        p["media_url"] = public_media_url(p["media_key"])
        post_list.append(p)

    return _ok({
        "username": user.username,
        "display_name": member["display_name"],
        "bio": member["bio"],
        "avatar_url": public_media_url(member["avatar_key"]) if member["avatar_key"] else "",
        "follower_count": member["follower_count"],
        "following_count": member["following_count"],
        "post_count": member["post_count"],
        "is_following": is_following,
        "is_own": viewer["id"] == member["id"],
        "posts": post_list,
    })


# ---------------------------------------------------------------------------
# FOLLOW / UNFOLLOW
# ---------------------------------------------------------------------------

@api_bp.post("/profile/<username>/follow")
@login_required
@require_tenant
def toggle_follow(username: str):
    from app.models import GlobalUser
    target_user = GlobalUser.query.filter_by(username=username).first()
    if not target_user:
        return _err("User not found.", 404)

    conn    = get_db()
    follower = _current_member(conn)
    target  = conn.execute(
        "SELECT * FROM members WHERE global_user_id=?", (target_user.id,)
    ).fetchone()

    if not target:
        return _err("Profile not found.", 404)

    target = dict(target)
    if target["id"] == follower["id"]:
        return _err("Cannot follow yourself.")

    existing = conn.execute(
        "SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?",
        (follower["id"], target["id"]),
    ).fetchone()

    if existing:
        conn.execute("DELETE FROM follows WHERE follower_id=? AND followed_id=?",
                     (follower["id"], target["id"]))
        conn.execute("UPDATE members SET follower_count=MAX(0,follower_count-1) WHERE id=?",
                     (target["id"],))
        conn.execute("UPDATE members SET following_count=MAX(0,following_count-1) WHERE id=?",
                     (follower["id"],))
        following = False
    else:
        conn.execute("INSERT OR IGNORE INTO follows (follower_id, followed_id) VALUES (?,?)",
                     (follower["id"], target["id"]))
        conn.execute("UPDATE members SET follower_count=follower_count+1 WHERE id=?",
                     (target["id"],))
        conn.execute("UPDATE members SET following_count=following_count+1 WHERE id=?",
                     (follower["id"],))
        conn.execute(
            """INSERT INTO notifications (recipient_id, actor_id, type)
               VALUES (?, ?, 'follow')""",
            (target["id"], follower["id"]),
        )
        following = True

    conn.commit()
    mark_dirty()
    return _ok({"following": following})


# ---------------------------------------------------------------------------
# NOTIFICATIONS
# ---------------------------------------------------------------------------

@api_bp.get("/notifications")
@login_required
@require_tenant
def get_notifications():
    conn   = get_db()
    member = _current_member(conn)

    rows = conn.execute(
        """SELECT n.*, m.display_name as actor_name, m.avatar_key as actor_avatar
           FROM notifications n
           LEFT JOIN members m ON m.id=n.actor_id
           WHERE n.recipient_id=?
           ORDER BY n.id DESC LIMIT 30""",
        (member["id"],),
    ).fetchall()

    notifs = []
    for r in rows:
        r = dict(r)
        r["actor_avatar_url"] = public_media_url(r["actor_avatar"]) if r.get("actor_avatar") else ""
        notifs.append(r)

    unread = sum(1 for n in notifs if not n["is_read"])
    return _ok(notifs, unread_count=unread)


@api_bp.post("/notifications/read")
@login_required
@require_tenant
def mark_notifications_read():
    conn   = get_db()
    member = _current_member(conn)
    conn.execute("UPDATE notifications SET is_read=1 WHERE recipient_id=?", (member["id"],))
    conn.commit()
    mark_dirty()
    return _ok({"marked_read": True})


# ---------------------------------------------------------------------------
# GIVING
# ---------------------------------------------------------------------------

@api_bp.post("/give")
@login_required
@require_tenant
@giving_rate_limit
def give():
    conn   = get_db()
    member = _current_member(conn)
    body   = request.get_json(silent=True) or {}

    try:
        amount = float(body.get("amount", 0))
    except (TypeError, ValueError):
        return _err("Invalid amount.")

    if amount <= 0:
        return _err("Amount must be greater than zero.")

    category  = body.get("category", "tithe")
    reference = f"REF-{uuid.uuid4().hex[:12].upper()}"

    conn.execute(
        """INSERT INTO giving (member_id, amount, category, reference)
           VALUES (?, ?, ?, ?)""",
        (member["id"], amount, category, reference),
    )
    conn.commit()
    mark_dirty()
    return _ok({"reference": reference, "status": "pending"}, status=201)


# ---------------------------------------------------------------------------
# EVENTS
# ---------------------------------------------------------------------------

@api_bp.get("/events")
@login_required
@require_tenant
def get_events():
    conn = get_db()
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        "SELECT * FROM events WHERE starts_at >= ? ORDER BY starts_at ASC LIMIT 20",
        (now,),
    ).fetchall()

    events = []
    for r in rows:
        r = dict(r)
        if r.get("banner_key"):
            r["banner_url"] = public_media_url(r["banner_key"])
        events.append(r)

    return _ok(events)


# ---------------------------------------------------------------------------
# EVENT RSVP
# ---------------------------------------------------------------------------

@api_bp.post("/events/<int:event_id>/rsvp")
@login_required
@require_tenant
def rsvp_event(event_id: int):
    conn   = get_db()
    member = _current_member(conn)
    body   = request.get_json(silent=True) or {}
    
    status = (body.get("status") or "going").strip()
    if status not in ("going", "interested", "not_going"):
        return _err("Invalid RSVP status.")
    
    # Check event exists
    event = conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        return _err("Event not found.", 404)
    
    conn.execute(
        "INSERT OR REPLACE INTO event_rsvps (event_id, member_id, status) VALUES (?, ?, ?)",
        (event_id, member["id"], status),
    )
    conn.commit()
    mark_dirty()
    
    return _ok({"status": status})


# ---------------------------------------------------------------------------
# FCM TOKEN REGISTRATION
# ---------------------------------------------------------------------------

@api_bp.post("/save-fcm-token")
@login_required
@require_tenant
def save_fcm_token():
    """Save or update a device's FCM token for push notifications."""
    conn   = get_db()
    member = _current_member(conn)
    body   = request.get_json(silent=True) or {}
    
    token    = (body.get("token") or "").strip()
    platform = (body.get("platform") or "android").strip()
    
    if not token:
        return _err("FCM token is required.")
    
    if platform not in ("android", "ios"):
        platform = "android"
    
    # Check if token already exists
    existing = conn.execute(
        "SELECT id FROM device_tokens WHERE member_id = ? AND fcm_token = ?",
        (member["id"], token),
    ).fetchone()
    
    if existing:
        # Update last_used timestamp
        conn.execute(
            "UPDATE device_tokens SET last_used = (strftime('%Y-%m-%dT%H:%M:%SZ','now')) WHERE id = ?",
            (existing["id"],),
        )
    else:
        # Insert new token
        conn.execute(
            "INSERT INTO device_tokens (member_id, fcm_token, platform) VALUES (?, ?, ?)",
            (member["id"], token, platform),
        )
    
    conn.commit()
    mark_dirty()
    
    return _ok({"saved": True})


@api_bp.delete("/device-tokens/<int:token_id>")
@login_required
@require_tenant
def delete_device_token(token_id: int):
    """Delete a specific device token (e.g., on logout or app uninstall)."""
    conn   = get_db()
    member = _current_member(conn)
    
    # Only allow deleting own tokens
    result = conn.execute(
        "DELETE FROM device_tokens WHERE id = ? AND member_id = ?",
        (token_id, member["id"]),
    )
    
    conn.commit()
    mark_dirty()
    
    if result.rowcount > 0:
        return _ok({"deleted": True})
    return _err("Token not found.", 404)


@api_bp.delete("/device-tokens")
@login_required
@require_tenant
def delete_all_device_tokens():
    """Delete all device tokens for the current user (e.g., on logout)."""
    conn   = get_db()
    member = _current_member(conn)
    
    conn.execute(
        "DELETE FROM device_tokens WHERE member_id = ?",
        (member["id"],),
    )
    
    conn.commit()
    mark_dirty()
    
    return _ok({"deleted": True})


# ---------------------------------------------------------------------------
# SEARCH
# ---------------------------------------------------------------------------

@api_bp.get("/search")
@login_required
@require_tenant
def search():
    q    = (request.args.get("q") or "").strip()
    conn = get_db()

    if not q:
        return _ok([])

    pattern = f"%{q}%"
    results = []
    
    # Search members
    rows = conn.execute(
        """SELECT m.*, u.username FROM members m
           JOIN global_users u ON u.id = m.global_user_id
           WHERE m.display_name LIKE ? OR u.username LIKE ?
           LIMIT 15""",
        (pattern, pattern),
    ).fetchall()

    from app.models import GlobalUser
    for r in rows:
        r = dict(r)
        gu = GlobalUser.query.get(r["global_user_id"])
        results.append({
            "type": "member",
            "username": gu.username if gu else "",
            "display_name": r["display_name"],
            "avatar_url": public_media_url(r["avatar_key"]) if r["avatar_key"] else "",
            "follower_count": r["follower_count"],
        })
    
    # Search posts (captions)
    post_rows = conn.execute(
        """SELECT p.id, p.caption, p.media_key, p.media_type, p.like_count, p.comment_count,
                  m.display_name, m.avatar_key
           FROM posts p
           JOIN members m ON m.id = p.member_id
           WHERE p.is_deleted = 0 AND p.caption LIKE ?
           ORDER BY p.id DESC LIMIT 10""",
        (pattern,),
    ).fetchall()
    
    for r in post_rows:
        r = dict(r)
        results.append({
            "type": "post",
            "id": r["id"],
            "caption": r["caption"][:100] if r["caption"] else "",
            "media_url": public_media_url(r["media_key"]),
            "display_name": r["display_name"],
            "avatar_url": public_media_url(r["avatar_key"]) if r["avatar_key"] else "",
            "like_count": r["like_count"],
            "comment_count": r["comment_count"],
        })
    
    # Search events
    event_rows = conn.execute(
        """SELECT e.id, e.title, e.description, e.location, e.starts_at, e.banner_key
           FROM events e
           WHERE e.title LIKE ? OR e.description LIKE ?
           ORDER BY e.starts_at ASC LIMIT 10""",
        (pattern, pattern),
    ).fetchall()
    
    for r in event_rows:
        r = dict(r)
        results.append({
            "type": "event",
            "id": r["id"],
            "title": r["title"],
            "description": r["description"][:100] if r["description"] else "",
            "location": r["location"],
            "starts_at": r["starts_at"],
            "banner_url": public_media_url(r["banner_key"]) if r.get("banner_key") else "",
        })

    return _ok(results)
