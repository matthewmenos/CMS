"""
Database models for COP Agona Ahanta ChMS.

Two database contexts:
  - Global DB  : authentication, church registry (Flask-SQLAlchemy, bound to app)
  - Tenant DB  : per-church social data (raw SQLite via sqlite3, loaded on request)
"""

import sqlite3
import hashlib
import os
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()  # bound to the global DB


# ---------------------------------------------------------------------------
# GLOBAL DATABASE MODELS
# ---------------------------------------------------------------------------

class Church(db.Model):
    """Registry of all church tenants."""
    __tablename__ = "churches"

    id        = db.Column(db.Integer, primary_key=True)
    name      = db.Column(db.String(120), nullable=False)
    slug      = db.Column(db.String(80), unique=True, nullable=False, index=True)
    logo_url  = db.Column(db.String(512), default="")
    db_key    = db.Column(db.String(256), nullable=False)   # R2 object key for the tenant .db
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_active  = db.Column(db.Boolean, default=True)

    members = db.relationship("GlobalUser", back_populates="church", lazy="dynamic")

    def __repr__(self):
        return f"<Church {self.slug}>"


class GlobalUser(UserMixin, db.Model):
    """
    Authentication record stored in the global DB.
    Thin: only the fields needed for login & routing.
    Rich profile lives in the tenant DB (Member table).
    """
    __tablename__ = "global_users"

    id           = db.Column(db.Integer, primary_key=True)
    church_id    = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    username     = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email        = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role         = db.Column(db.String(20), default="member")   # member | moderator | admin
    is_active    = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login   = db.Column(db.DateTime, nullable=True)

    church = db.relationship("Church", back_populates="members")

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    def get_id(self):
        return str(self.id)

    def __repr__(self):
        return f"<GlobalUser {self.username} church={self.church_id}>"


# ---------------------------------------------------------------------------
# TENANT DATABASE  — raw sqlite3 helpers
# ---------------------------------------------------------------------------

_TENANT_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS members (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    global_user_id INTEGER NOT NULL UNIQUE,   -- mirrors GlobalUser.id
    display_name TEXT    NOT NULL,
    bio          TEXT    DEFAULT '',
    avatar_key   TEXT    DEFAULT '',           -- R2 object key
    website      TEXT    DEFAULT '',
    joined_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    follower_count  INTEGER DEFAULT 0,
    following_count INTEGER DEFAULT 0,
    post_count      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS follows (
    follower_id  INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    followed_id  INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (follower_id, followed_id)
);

CREATE TABLE IF NOT EXISTS posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id   INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    media_key   TEXT    NOT NULL,          -- R2 object key (image/video)
    media_type  TEXT    NOT NULL DEFAULT 'image',   -- image | video | reel
    caption     TEXT    DEFAULT '',
    location    TEXT    DEFAULT '',
    like_count  INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    is_deleted  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id   INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    media_key   TEXT    NOT NULL,
    media_type  TEXT    NOT NULL DEFAULT 'image',
    caption     TEXT    DEFAULT '',
    expires_at  TEXT    NOT NULL,          -- ISO-8601, 24 h after creation
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    is_deleted  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS likes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id   INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(post_id, member_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id    INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    member_id  INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    parent_id  INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    body       TEXT    NOT NULL,
    like_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    is_deleted INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS comment_likes (
    comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
    member_id  INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    PRIMARY KEY (comment_id, member_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    actor_id     INTEGER REFERENCES members(id) ON DELETE SET NULL,
    type         TEXT    NOT NULL,   -- like | comment | follow | mention
    post_id      INTEGER REFERENCES posts(id) ON DELETE CASCADE,
    comment_id   INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    is_read      INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT DEFAULT '',
    location    TEXT DEFAULT '',
    banner_key  TEXT DEFAULT '',
    starts_at   TEXT NOT NULL,
    ends_at     TEXT,
    created_by  INTEGER REFERENCES members(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS giving (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id   INTEGER REFERENCES members(id) ON DELETE SET NULL,
    amount      REAL    NOT NULL,
    currency    TEXT    DEFAULT 'GHS',
    category    TEXT    DEFAULT 'tithe',   -- tithe | offering | pledge | special
    reference   TEXT    UNIQUE,
    status      TEXT    DEFAULT 'pending', -- pending | confirmed | failed
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_posts_member   ON posts(member_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_type     ON posts(media_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_likes_post     ON likes(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_post  ON comments(post_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_notif_recipient ON notifications(recipient_id, is_read, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stories_member  ON stories(member_id, expires_at DESC);
"""


def init_tenant_db(db_path: str) -> None:
    """Create all tenant tables if they don't exist."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_TENANT_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_tenant_conn(db_path: str) -> sqlite3.Connection:
    """Return a WAL-mode connection with row_factory set."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-8000")   # 8 MB page cache
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
