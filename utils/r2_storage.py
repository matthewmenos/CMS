"""
utils/r2_storage.py — Cloudflare R2 (S3-compatible) storage utilities.

Responsibilities:
  1. Upload / download the GLOBAL SQLite .db (global.db) to/from R2.
  2. Upload / download TENANT SQLite .db files to/from R2 (one per church).
  3. Upload media assets (images, videos, audio) to R2 (media_bucket).
  4. Generate short-lived presigned PUT URLs for direct client → R2 uploads.
  5. Generate presigned GET URLs for private media serving.
  6. Delete objects (when posts or accounts are removed).

Both global.db and tenant .db files are stored in R2_DB_BUCKET.
All operations have retry logic and structured logging.
"""

import logging
import hashlib
import threading
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError, BotoCoreError
from botocore.config import Config as BotoConfig
from flask import current_app

logger = logging.getLogger(__name__)

# Thread-local R2 client — safe reuse within a single thread
_thread_local = threading.local()

# R2 key used for the global auth database
GLOBAL_DB_R2_KEY = "global.db"


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT FACTORY
# ─────────────────────────────────────────────────────────────────────────────

def _get_client():
    """
    Return a boto3 S3 client configured for Cloudflare R2.
    Creates once per thread, then reuses for performance.
    """
    if not hasattr(_thread_local, "r2_client"):
        _thread_local.r2_client = boto3.client(
            "s3",
            endpoint_url=current_app.config["R2_ENDPOINT_URL"],
            aws_access_key_id=current_app.config["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=current_app.config["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
            config=BotoConfig(
                retries={"max_attempts": 3, "mode": "adaptive"},
                max_pool_connections=50,
                tcp_keepalive=True,
            ),
        )
    return _thread_local.r2_client


def _make_bare_client(cfg: dict):
    """
    Create a standalone boto3 client from a plain config dict.
    Used during app startup BEFORE Flask's app context is available.
    """
    return boto3.client(
        "s3",
        endpoint_url=cfg["R2_ENDPOINT_URL"],
        aws_access_key_id=cfg["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=cfg["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=BotoConfig(retries={"max_attempts": 3, "mode": "adaptive"}),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL DATABASE  — download / upload
# ─────────────────────────────────────────────────────────────────────────────

def download_global_db(local_path: Path, cfg: dict) -> bool:
    """
    Download global.db from R2 to local disk.
    Called BEFORE the Flask app context exists (bare client).

    Args:
        local_path: Destination on local filesystem, e.g. Path('/tmp/global.db')
        cfg:        Raw config dict with R2_* keys.

    Returns:
        True if a fresh download occurred, False if already up-to-date or not found.
    """
    bucket = cfg["R2_DB_BUCKET"]
    client = _make_bare_client(cfg)

    local_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        head        = client.head_object(Bucket=bucket, Key=GLOBAL_DB_R2_KEY)
        remote_etag = head.get("ETag", "").strip('"')

        # Skip download if local copy matches ETag (MD5)
        if local_path.exists() and remote_etag:
            if _md5_of_file(local_path) == remote_etag:
                logger.info("global.db is up-to-date (ETag match), skipping download.")
                return False

        logger.info("Downloading global.db from R2 bucket '%s'…", bucket)
        client.download_file(bucket, GLOBAL_DB_R2_KEY, str(local_path))
        logger.info("global.db restored to %s", local_path)
        return True

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("404", "NoSuchKey"):
            logger.info("global.db not found in R2 — will be created fresh on first run.")
            return False
        logger.error("R2 error downloading global.db: %s", exc)
        raise


def upload_global_db(local_path: Path) -> None:
    """
    Upload global.db back to R2 after a write.
    Called within an active Flask app context (uses thread-local client).

    Args:
        local_path: Source path, e.g. Path('/tmp/global.db')
    """
    if not local_path.exists():
        logger.warning("upload_global_db: file not found at %s — skipping.", local_path)
        return

    bucket = current_app.config["R2_DB_BUCKET"]
    try:
        _get_client().upload_file(
            str(local_path),
            bucket,
            GLOBAL_DB_R2_KEY,
            ExtraArgs={
                "ContentType": "application/x-sqlite3",
                "Metadata": {
                    "uploaded-at": datetime.now(timezone.utc).isoformat(),
                    "source": "cop-agona-global",
                },
            },
        )
        logger.debug("global.db synced → R2 bucket '%s'.", bucket)
    except (ClientError, BotoCoreError) as exc:
        logger.error("Failed to upload global.db to R2: %s", exc)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# TENANT DATABASE — download / upload / exists
# ─────────────────────────────────────────────────────────────────────────────

def db_exists_in_r2(db_key: str) -> bool:
    """Check whether a tenant .db object exists in R2 without downloading it."""
    bucket = current_app.config["R2_DB_BUCKET"]
    try:
        _get_client().head_object(Bucket=bucket, Key=db_key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        logger.error("R2 head_object error for %s: %s", db_key, exc)
        raise


def download_tenant_db(db_key: str, local_path: Path) -> bool:
    """
    Download a tenant SQLite file from R2.
    ETag check avoids unnecessary downloads.

    Returns:
        True if a fresh download occurred, False if already current or not found.
    """
    bucket = current_app.config["R2_DB_BUCKET"]
    client = _get_client()

    local_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        head        = client.head_object(Bucket=bucket, Key=db_key)
        remote_etag = head.get("ETag", "").strip('"')

        if local_path.exists() and remote_etag:
            if _md5_of_file(local_path) == remote_etag:
                logger.debug("Tenant DB %s up-to-date, skipping download.", db_key)
                return False

        logger.info("Downloading tenant DB %s from R2…", db_key)
        client.download_file(bucket, db_key, str(local_path))
        logger.info("Tenant DB %s → %s", db_key, local_path)
        return True

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("404", "NoSuchKey"):
            logger.info("Tenant DB %s not in R2 — fresh DB will be created.", db_key)
            return False
        logger.error("R2 error downloading tenant DB %s: %s", db_key, exc)
        raise


def upload_tenant_db(db_key: str, local_path: Path) -> dict:
    """Upload a tenant SQLite file to R2."""
    bucket = current_app.config["R2_DB_BUCKET"]

    if not local_path.exists():
        raise FileNotFoundError(f"Tenant DB not found locally: {local_path}")

    logger.info("Uploading tenant DB %s → R2 bucket '%s'…", db_key, bucket)
    try:
        resp = _get_client().upload_file(
            str(local_path),
            bucket,
            db_key,
            ExtraArgs={
                "ContentType": "application/x-sqlite3",
                "Metadata": {
                    "uploaded-at": datetime.now(timezone.utc).isoformat(),
                    "source": "cop-agona-tenant",
                },
            },
        )
        logger.info("Tenant DB %s synced → R2.", db_key)
        return resp or {}
    except (ClientError, BotoCoreError) as exc:
        logger.error("Failed to upload tenant DB %s: %s", db_key, exc)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA — presigned PUT (direct client upload)
# ─────────────────────────────────────────────────────────────────────────────

def generate_presigned_put_url(
    object_key: str,
    content_type: str,
    expiry_seconds: Optional[int] = None,
) -> str:
    """
    Generate a presigned PUT URL. Client uploads directly to R2 — Flask
    never receives the file bytes.

    Args:
        object_key:     R2 object key, e.g. 'media/posts/abc123.mp4'
        content_type:   MIME type (must match what client sends in Content-Type header)
        expiry_seconds: URL lifetime. Defaults to R2_PRESIGN_EXPIRY config.

    Returns:
        Presigned URL string.
    """
    bucket = current_app.config["R2_MEDIA_BUCKET"]
    expiry = expiry_seconds or current_app.config.get("R2_PRESIGN_EXPIRY", 3600)

    try:
        return _get_client().generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": object_key, "ContentType": content_type},
            ExpiresIn=expiry,
            HttpMethod="PUT",
        )
    except (ClientError, BotoCoreError) as exc:
        logger.error("Presigned PUT URL failed for %s: %s", object_key, exc)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA — presigned GET (serve private objects)
# ─────────────────────────────────────────────────────────────────────────────

def generate_presigned_get_url(
    object_key: str,
    expiry_seconds: Optional[int] = None,
    bucket: Optional[str] = None,
) -> str:
    """Generate a presigned GET URL for a private R2 object."""
    _bucket = bucket or current_app.config["R2_MEDIA_BUCKET"]
    expiry  = expiry_seconds or current_app.config.get("R2_PRESIGN_EXPIRY", 3600)
    try:
        return _get_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": _bucket, "Key": object_key},
            ExpiresIn=expiry,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.error("Presigned GET URL failed for %s: %s", object_key, exc)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA — small uploads via Flask (avatars, thumbnails < 10 MB)
# ─────────────────────────────────────────────────────────────────────────────

def upload_media_bytes(
    file_bytes: bytes,
    object_key: str,
    content_type: str,
    bucket: Optional[str] = None,
) -> str:
    """Upload raw bytes to R2 (avatars, small thumbnails passed through Flask)."""
    _bucket = bucket or current_app.config["R2_MEDIA_BUCKET"]
    try:
        _get_client().put_object(
            Bucket=_bucket, Key=object_key,
            Body=file_bytes, ContentType=content_type,
        )
        logger.info("Uploaded %d bytes → R2 key: %s", len(file_bytes), object_key)
        return object_key
    except (ClientError, BotoCoreError) as exc:
        logger.error("upload_media_bytes failed for %s: %s", object_key, exc)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA — delete
# ─────────────────────────────────────────────────────────────────────────────

def delete_r2_object(object_key: str, bucket: Optional[str] = None) -> bool:
    """Delete a single R2 object."""
    _bucket = bucket or current_app.config["R2_MEDIA_BUCKET"]
    try:
        _get_client().delete_object(Bucket=_bucket, Key=object_key)
        logger.info("Deleted R2 object: %s", object_key)
        return True
    except (ClientError, BotoCoreError) as exc:
        logger.error("delete_r2_object failed for %s: %s", object_key, exc)
        raise


def delete_r2_objects(object_keys: list, bucket: Optional[str] = None) -> int:
    """Batch-delete up to 1000 R2 objects per call."""
    _bucket = bucket or current_app.config["R2_MEDIA_BUCKET"]
    if not object_keys:
        return 0
    deleted = 0
    for chunk in _chunked(object_keys, 1000):
        payload = {"Objects": [{"Key": k} for k in chunk], "Quiet": True}
        try:
            resp     = _get_client().delete_objects(Bucket=_bucket, Delete=payload)
            deleted += len(chunk) - len(resp.get("Errors", []))
        except (ClientError, BotoCoreError) as exc:
            logger.error("Batch delete error: %s", exc)
    return deleted


# ─────────────────────────────────────────────────────────────────────────────
# OBJECT KEY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def build_media_key(church_slug: str, media_type: str, filename: str) -> str:
    """e.g. build_media_key('cop-agona','posts','abc.mp4') → 'cop-agona/posts/abc.mp4'"""
    return f"{church_slug}/{media_type}/{filename}"


def build_db_key(church_slug: str) -> str:
    """e.g. build_db_key('cop-agona-ahanta') → 'cop-agona-ahanta.db'"""
    return f"{church_slug}.db"


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _md5_of_file(path: Path) -> str:
    """Compute MD5 hex digest of a file for ETag comparison."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _chunked(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i: i + size]
