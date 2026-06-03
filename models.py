"""
models.py — SQLAlchemy schema for COP Agona Ahanta ChMS.

TWO database scopes:
─────────────────────────────────────────────────────────────────────────────
1. GLOBAL DATABASE  (instance/global.db)
   • User          — authentication, roles
   • Church        — tenant registry
   • Notification  — cross-church notifications

2. TENANT DATABASE  (instance/tenants/<slug>.db)  — one per church
   • Member        — church member profile (extends global User)
   • Post          — feed posts (image / video / text)
   • Story         — 24-hour ephemeral stories
   • Like          — polymorphic likes (Post or Reel)
   • Comment       — threaded comments on Posts
   • Reel          — short sermon video clips
   • GivingRecord  — tithe / offering records
   • DevotionalDay — daily devotional content

All models include created_at / updated_at for consistent auditing.
"""

from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _now() -> datetime:
    """Return timezone-aware UTC now — consistent across all models."""
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# ── GLOBAL DATABASE MODELS ────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

class User(UserMixin, db.Model):
    """
    Global authentication record.
    One user can belong to many churches via the Church.members relationship.
    """
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(255), unique=True, nullable=False, index=True)
    username      = db.Column(db.String(80),  unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    display_name  = db.Column(db.String(120), nullable=True)
    avatar_url    = db.Column(db.String(512), nullable=True)
    role          = db.Column(
        db.String(20),
        nullable=False,
        default="member",
        # Roles: 'superadmin' | 'admin' | 'pastor' | 'member'
    )
    is_active     = db.Column(db.Boolean, default=True, nullable=False)
    last_seen     = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at    = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)
    updated_at    = db.Column(db.DateTime(timezone=True), default=_now, onupdate=_now)

    # Relationships
    notifications = db.relationship("Notification", back_populates="user",
                                    lazy="dynamic", cascade="all, delete-orphan")

    # ── Password helpers ──────────────────────────────────────────────────────
    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "email":        self.email,
            "username":     self.username,
            "display_name": self.display_name,
            "avatar_url":   self.avatar_url,
            "role":         self.role,
            "created_at":   self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<User {self.username} [{self.role}]>"


class Church(db.Model):
    """
    Tenant registry. Each row maps to one isolated SQLite .db file in R2.
    """
    __tablename__ = "churches"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(200), nullable=False)
    slug        = db.Column(db.String(80),  unique=True, nullable=False, index=True)
    tagline     = db.Column(db.String(300), nullable=True)
    logo_url    = db.Column(db.String(512), nullable=True)
    location    = db.Column(db.String(200), nullable=True)
    # R2 key of the tenant .db file, e.g. "cop-agona-ahanta.db"
    db_r2_key   = db.Column(db.String(255), nullable=True)
    is_active   = db.Column(db.Boolean, default=True, nullable=False)
    created_at  = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)
    updated_at  = db.Column(db.DateTime(timezone=True), default=_now, onupdate=_now)

    def to_dict(self) -> dict:
        return {
            "id":         self.id,
            "name":       self.name,
            "slug":       self.slug,
            "tagline":    self.tagline,
            "logo_url":   self.logo_url,
            "location":   self.location,
            "is_active":  self.is_active,
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<Church {self.slug}>"


class Notification(db.Model):
    """
    System notifications: new likes, comments, giving receipts, broadcasts.
    Stored globally so users see them regardless of which church they're in.
    """
    __tablename__ = "notifications"

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    church_slug = db.Column(db.String(80), nullable=False, index=True)
    notif_type  = db.Column(
        db.String(30), nullable=False
        # Types: 'like' | 'comment' | 'follow' | 'giving' | 'broadcast' | 'story'
    )
    message     = db.Column(db.Text, nullable=False)
    link        = db.Column(db.String(512), nullable=True)   # Deep link inside app
    is_read     = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at  = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)

    # Relationships
    user = db.relationship("User", back_populates="notifications")

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "notif_type":   self.notif_type,
            "message":      self.message,
            "link":         self.link,
            "is_read":      self.is_read,
            "created_at":   self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<Notification [{self.notif_type}] -> user {self.user_id}>"


# ═══════════════════════════════════════════════════════════════════════════════
# ── TENANT DATABASE MODELS ────────────────────────────────────────────────────
# Each church gets its own isolated SQLite file. These models are bound to a
# *separate* SQLAlchemy engine via db_router.get_tenant_session().
# We define them as plain declarative classes (NOT bound to the global `db`)
# so they can be re-used with any engine.
# ═══════════════════════════════════════════════════════════════════════════════

from sqlalchemy.orm import DeclarativeBase, relationship, Mapped, mapped_column
from sqlalchemy import (
    Integer, String, Text, Boolean, DateTime, ForeignKey,
    Index, event, func
)


class TenantBase(DeclarativeBase):
    """Base class for all tenant-scoped models."""
    pass


class Member(TenantBase):
    """
    Church-specific member profile. Links back to the global User via
    global_user_id (stored as a plain integer — no FK cross-DB).
    """
    __tablename__ = "members"

    id             = db.Column(Integer, primary_key=True)
    global_user_id = db.Column(Integer, nullable=False, unique=True, index=True)
    username       = db.Column(String(80),  nullable=False, index=True)
    display_name   = db.Column(String(120), nullable=True)
    avatar_url     = db.Column(String(512), nullable=True)
    bio            = db.Column(Text,        nullable=True)
    phone          = db.Column(String(30),  nullable=True)
    # Church roles within this tenant
    church_role    = db.Column(String(40), default="member", nullable=False)
    department     = db.Column(String(100), nullable=True)   # e.g. "Youth", "Choir"
    is_verified    = db.Column(Boolean, default=False, nullable=False)
    followers_count = db.Column(Integer, default=0, nullable=False)
    following_count = db.Column(Integer, default=0, nullable=False)
    posts_count     = db.Column(Integer, default=0, nullable=False)
    created_at     = db.Column(DateTime, default=_now, nullable=False)
    updated_at     = db.Column(DateTime, default=_now, onupdate=_now)

    # Relationships
    posts    = relationship("Post",    back_populates="author",  lazy="dynamic",
                            cascade="all, delete-orphan")
    stories  = relationship("Story",   back_populates="author",  lazy="dynamic",
                            cascade="all, delete-orphan")
    reels    = relationship("Reel",    back_populates="author",  lazy="dynamic",
                            cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="author",  lazy="dynamic",
                            cascade="all, delete-orphan")
    givings  = relationship("GivingRecord", back_populates="member", lazy="dynamic",
                            cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id":            self.id,
            "username":      self.username,
            "display_name":  self.display_name,
            "avatar_url":    self.avatar_url,
            "bio":           self.bio,
            "church_role":   self.church_role,
            "department":    self.department,
            "is_verified":   self.is_verified,
            "followers":     self.followers_count,
            "following":     self.following_count,
            "posts":         self.posts_count,
        }

    def __repr__(self) -> str:
        return f"<Member {self.username}>"


class Post(TenantBase):
    """
    A feed post — image, video, or text-only (sermon note, announcement).
    """
    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_author_created", "author_id", "created_at"),
    )

    id          = db.Column(Integer, primary_key=True)
    author_id   = db.Column(Integer, ForeignKey("members.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    # Media stored in R2; this is the full public/signed URL or R2 object key
    media_url   = db.Column(String(1024), nullable=True)
    media_type  = db.Column(String(20),   nullable=True)
    # Types: 'image' | 'video' | 'text' | 'audio'
    thumbnail_url = db.Column(String(1024), nullable=True)  # For videos
    caption     = db.Column(Text, nullable=True)
    tags        = db.Column(String(500), nullable=True)     # Comma-separated hashtags
    is_pinned   = db.Column(Boolean, default=False, nullable=False)
    likes_count = db.Column(Integer, default=0, nullable=False)
    comments_count = db.Column(Integer, default=0, nullable=False)
    views_count = db.Column(Integer, default=0, nullable=False)
    created_at  = db.Column(DateTime, default=_now, nullable=False, index=True)
    updated_at  = db.Column(DateTime, default=_now, onupdate=_now)

    # Relationships
    author   = relationship("Member",  back_populates="posts")
    likes    = relationship("Like",    back_populates="post",
                            lazy="dynamic", cascade="all, delete-orphan",
                            foreign_keys="Like.post_id")
    comments = relationship("Comment", back_populates="post",
                            lazy="dynamic", cascade="all, delete-orphan")

    def to_dict(self, current_member_id: int = None) -> dict:
        return {
            "id":             self.id,
            "author":         self.author.to_dict() if self.author else None,
            "media_url":      self.media_url,
            "media_type":     self.media_type,
            "thumbnail_url":  self.thumbnail_url,
            "caption":        self.caption,
            "tags":           self.tags.split(",") if self.tags else [],
            "is_pinned":      self.is_pinned,
            "likes_count":    self.likes_count,
            "comments_count": self.comments_count,
            "views_count":    self.views_count,
            "created_at":     self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<Post {self.id} by member {self.author_id}>"


class Story(TenantBase):
    """
    Ephemeral 24-hour story — image or short video (like Instagram Stories).
    Used for daily devotionals, quick announcements, event countdowns.
    """
    __tablename__ = "stories"

    id          = db.Column(Integer, primary_key=True)
    author_id   = db.Column(Integer, ForeignKey("members.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    media_url   = db.Column(String(1024), nullable=False)
    media_type  = db.Column(String(20),   nullable=False, default="image")
    caption     = db.Column(String(300),  nullable=True)
    bg_color    = db.Column(String(20),   nullable=True)   # e.g. "#1A73E8"
    views_count = db.Column(Integer, default=0, nullable=False)
    expires_at  = db.Column(DateTime, nullable=False)       # +24h from created_at
    created_at  = db.Column(DateTime, default=_now, nullable=False, index=True)

    # Relationships
    author = relationship("Member", back_populates="stories")

    def is_expired(self) -> bool:
        return _now() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "id":          self.id,
            "author":      self.author.to_dict() if self.author else None,
            "media_url":   self.media_url,
            "media_type":  self.media_type,
            "caption":     self.caption,
            "bg_color":    self.bg_color,
            "views_count": self.views_count,
            "expires_at":  self.expires_at.isoformat(),
            "created_at":  self.created_at.isoformat(),
            "is_expired":  self.is_expired(),
        }

    def __repr__(self) -> str:
        return f"<Story {self.id} by member {self.author_id}>"


class Like(TenantBase):
    """
    Polymorphic like — can target a Post or a Reel.
    Unique constraint prevents double-liking.
    """
    __tablename__ = "likes"
    __table_args__ = (
        db.UniqueConstraint("member_id", "post_id",   name="uq_like_post"),
        db.UniqueConstraint("member_id", "reel_id",   name="uq_like_reel"),
    )

    id         = db.Column(Integer, primary_key=True)
    member_id  = db.Column(Integer, ForeignKey("members.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    post_id    = db.Column(Integer, ForeignKey("posts.id",   ondelete="CASCADE"),
                           nullable=True, index=True)
    reel_id    = db.Column(Integer, ForeignKey("reels.id",   ondelete="CASCADE"),
                           nullable=True, index=True)
    created_at = db.Column(DateTime, default=_now, nullable=False)

    # Relationships
    member = relationship("Member")
    post   = relationship("Post",  back_populates="likes", foreign_keys=[post_id])
    reel   = relationship("Reel",  back_populates="likes", foreign_keys=[reel_id])

    def __repr__(self) -> str:
        target = f"post={self.post_id}" if self.post_id else f"reel={self.reel_id}"
        return f"<Like member={self.member_id} {target}>"


class Comment(TenantBase):
    """
    Threaded comment. parent_id allows one level of replies (like Instagram).
    """
    __tablename__ = "comments"
    __table_args__ = (
        Index("ix_comments_post_created", "post_id", "created_at"),
    )

    id         = db.Column(Integer, primary_key=True)
    post_id    = db.Column(Integer, ForeignKey("posts.id",    ondelete="CASCADE"),
                           nullable=False, index=True)
    author_id  = db.Column(Integer, ForeignKey("members.id",  ondelete="CASCADE"),
                           nullable=False, index=True)
    parent_id  = db.Column(Integer, ForeignKey("comments.id", ondelete="CASCADE"),
                           nullable=True,  index=True)
    body       = db.Column(Text, nullable=False)
    likes_count = db.Column(Integer, default=0, nullable=False)
    created_at = db.Column(DateTime, default=_now, nullable=False)
    updated_at = db.Column(DateTime, default=_now, onupdate=_now)

    # Relationships
    author  = relationship("Member",  back_populates="comments")
    post    = relationship("Post",    back_populates="comments")
    replies = relationship("Comment", backref=db.backref("parent", remote_side=[id]),
                           lazy="dynamic", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id":          self.id,
            "author":      self.author.to_dict() if self.author else None,
            "body":        self.body,
            "parent_id":   self.parent_id,
            "likes_count": self.likes_count,
            "created_at":  self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<Comment {self.id} on post {self.post_id}>"


class Reel(TenantBase):
    """
    Short sermon / worship video clip (Instagram Reels equivalent).
    Vertical format. Stored in R2.
    """
    __tablename__ = "reels"

    id            = db.Column(Integer, primary_key=True)
    author_id     = db.Column(Integer, ForeignKey("members.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    video_url     = db.Column(String(1024), nullable=False)
    thumbnail_url = db.Column(String(1024), nullable=True)
    caption       = db.Column(Text, nullable=True)
    duration_secs = db.Column(Integer, nullable=True)   # Video length in seconds
    likes_count   = db.Column(Integer, default=0, nullable=False)
    comments_count = db.Column(Integer, default=0, nullable=False)
    views_count   = db.Column(Integer, default=0, nullable=False)
    is_featured   = db.Column(Boolean, default=False, nullable=False)
    created_at    = db.Column(DateTime, default=_now, nullable=False, index=True)
    updated_at    = db.Column(DateTime, default=_now, onupdate=_now)

    # Relationships
    author   = relationship("Member",  back_populates="reels")
    likes    = relationship("Like",    back_populates="reel",
                            lazy="dynamic", cascade="all, delete-orphan",
                            foreign_keys="Like.reel_id")

    def to_dict(self) -> dict:
        return {
            "id":             self.id,
            "author":         self.author.to_dict() if self.author else None,
            "video_url":      self.video_url,
            "thumbnail_url":  self.thumbnail_url,
            "caption":        self.caption,
            "duration_secs":  self.duration_secs,
            "likes_count":    self.likes_count,
            "comments_count": self.comments_count,
            "views_count":    self.views_count,
            "is_featured":    self.is_featured,
            "created_at":     self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<Reel {self.id} by member {self.author_id}>"


class GivingRecord(TenantBase):
    """
    Tithe, offering, or special giving record.
    Actual payment processed externally (Paystack / MTN MoMo).
    This table stores confirmed transactions.
    """
    __tablename__ = "giving_records"

    id              = db.Column(Integer, primary_key=True)
    member_id       = db.Column(Integer, ForeignKey("members.id", ondelete="SET NULL"),
                                nullable=True, index=True)
    amount          = db.Column(db.Float,  nullable=False)
    currency        = db.Column(String(5), nullable=False, default="GHS")
    giving_type     = db.Column(String(40), nullable=False, default="tithe")
    # Types: 'tithe' | 'offering' | 'special' | 'building_fund' | 'missions'
    reference       = db.Column(String(200), unique=True, nullable=True, index=True)
    payment_method  = db.Column(String(40),  nullable=True)   # 'momo' | 'card' | 'cash'
    is_anonymous    = db.Column(Boolean, default=False, nullable=False)
    notes           = db.Column(Text, nullable=True)
    created_at      = db.Column(DateTime, default=_now, nullable=False, index=True)

    # Relationships
    member = relationship("Member", back_populates="givings")

    def to_dict(self, include_member: bool = False) -> dict:
        data = {
            "id":             self.id,
            "amount":         self.amount,
            "currency":       self.currency,
            "giving_type":    self.giving_type,
            "reference":      self.reference,
            "payment_method": self.payment_method,
            "is_anonymous":   self.is_anonymous,
            "created_at":     self.created_at.isoformat(),
        }
        if include_member and not self.is_anonymous and self.member:
            data["member"] = self.member.to_dict()
        return data

    def __repr__(self) -> str:
        return f"<GivingRecord {self.reference} {self.amount} {self.currency}>"


class DevotionalDay(TenantBase):
    """
    Daily devotional — pastor publishes scripture, reflection, and prayer.
    Shown in the Stories bar at the top of the feed.
    """
    __tablename__ = "devotionals"

    id          = db.Column(Integer, primary_key=True)
    author_id   = db.Column(Integer, ForeignKey("members.id", ondelete="SET NULL"),
                            nullable=True)
    title       = db.Column(String(300), nullable=False)
    scripture   = db.Column(String(300), nullable=True)    # e.g. "John 3:16"
    scripture_text = db.Column(Text, nullable=True)
    reflection  = db.Column(Text, nullable=False)
    prayer      = db.Column(Text, nullable=True)
    cover_image_url = db.Column(String(1024), nullable=True)
    day_date    = db.Column(db.Date, nullable=False, unique=True, index=True)
    created_at  = db.Column(DateTime, default=_now, nullable=False)

    author = relationship("Member")

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "title":            self.title,
            "scripture":        self.scripture,
            "scripture_text":   self.scripture_text,
            "reflection":       self.reflection,
            "prayer":           self.prayer,
            "cover_image_url":  self.cover_image_url,
            "day_date":         self.day_date.isoformat(),
            "created_at":       self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<DevotionalDay {self.day_date}: {self.title}>"
