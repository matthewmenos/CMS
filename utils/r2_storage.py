"""
utils/r2_storage.py — Cloudflare R2 (S3-compatible) storage utilities.

Responsibilities:
  1. Upload / download tenant SQLite .db files to/from R2 (db_bucket).
  2. Upload media assets (images, videos, audio) to R2 (media_bucket).
  3. Generate short-lived presigned PUT URLs so clients can upload
     large media DIRECTLY to R2 — bypassing Flask entirely.
  4. Generate presigned GET URLs for private media serving.
  5. Delete objects (when posts or accounts are removed).

All operations are synchronous with retry logic and structured logging.
"""

import os
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

# Thread-local storage for per-request R2 client (thread-safe reuse)
_thread_local = threading.local()


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT FACTORY
# ─────────────────────────────────────────────────────────────────────────────

def _get_client():
    """
    Return a boto3 S3 client configured for Cloudflare R2.
    Reuses the same client within a thread for performance.
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


# ─────────────────────────────────────────────────────────────────────────────
# TENANT DATABASE: DOWNLOAD / UPLOAD / EXISTS
# ─────────────────────────────────────────────────────────────────────────────

def db_exists_in_r2(db_key: str) -> bool:
    """
    Check if a tenant .db file exists in R2 without downloading it.

    Args:
        db_key: R2 object key, e.g. "cop-agona-ahanta.db"

    Returns:
        True if the object exists, False otherwise.
    """
    bucket = current_app.config["R2_DB_BUCKET"]
    try:
        _get_client().head_object(Bucket=bucket, Key=db_key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        logger.error("R2 head_object failed for %s: %s", db_key, exc)
        raise


def download_tenant_db(db_key: str, local_path: Path) -> bool:
    """
    Download a tenant SQLite file from R2 to local disk.
    Compares ETag (MD5) to avoid unnecessary downloads.

    Args:
        db_key:     R2 object key, e.g. "cop-agona-ahanta.db"
        local_path: Destination path on local filesystem.

    Returns:
        True if a fresh download occurred, False if already up-to-date.
    """
    bucket = current_app.config["R2_DB_BUCKET"]
    client = _get_client()

    # Ensure parent directory exists
    local_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Get remote ETag
        head = client.head_object(Bucket=bucket, Key=db_key)
        remote_etag = head.get("ETag", "").strip('"')

        # Compare with local MD5 if file already exists
        if local_path.exists() and remote_etag:
            local_md5 = _md5_of_file(local_path)
            if local_md5 == remote_etag:
                logger.debug("DB %s is up-to-date (ETag match), skipping download.", db_key)
                return False  # Already current

        # Download
        logger.info("Downloading tenant DB %s from R2 bucket '%s'...", db_key, bucket)
        client.download_file(bucket, db_key, str(local_path))
        logger.info("Downloaded %s → %s", db_key, local_path)
        return True

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("404", "NoSuchKey"):
            logger.info("Tenant DB %s not found in R2; will create fresh.", db_key)
            return False
        logger.error("Failed to download tenant DB %s: %s", db_key, exc)
        raise
    except Exception as exc:
        logger.error("Unexpected error downloading tenant DB %s: %s", db_key, exc)
        raise


def upload_tenant_db(db_key: str, local_path: Path) -> dict:
    """
    Upload a local tenant SQLite file back to R2.
    Uses multipart upload automatically for large files via boto3.

    Args:
        db_key:     R2 object key, e.g. "cop-agona-ahanta.db"
        local_path: Source path on local filesystem.

    Returns:
        boto3 response dict.
    """
    bucket = current_app.config["R2_DB_BUCKET"]

    if not local_path.exists():
        raise FileNotFoundError(f"Local DB file not found: {local_path}")

    logger.info("Uploading tenant DB %s → R2 bucket '%s'...", local_path.name, bucket)
    try:
        response = _get_client().upload_file(
            str(local_path),
            bucket,
            db_key,
            ExtraArgs={
                "ContentType": "application/x-sqlite3",
                "Metadata": {
                    "uploaded-at": datetime.now(timezone.utc).isoformat(),
                    "source":      "cop-agona-ahanta-chms",
                },
            },
        )
        logger.info("Uploaded %s to R2 bucket '%s'.", db_key, bucket)
        return response or {}
    except (ClientError, BotoCoreError) as exc:
        logger.error("Failed to upload tenant DB %s: %s", db_key, exc)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA: PRESIGNED PUT (direct client upload)
# ─────────────────────────────────────────────────────────────────────────────

def generate_presigned_put_url(
    object_key: str,
    content_type: str,
    expiry_seconds: Optional[int] = None,
) -> str:
    """
    Generate a presigned PUT URL for the client to upload media DIRECTLY to R2.
    Flask never sees the file bytes — only the object key after upload completes.

    Args:
        object_key:   R2 object key, e.g. "media/posts/uuid.mp4"
        content_type: MIME type of the file (must match what client sends in PUT).
        expiry_seconds: URL lifetime in seconds. Defaults to config value.

    Returns:
        Presigned URL string.

    Usage (client-side JS):
        const { url } = await fetch('/api/upload/presign', {...}).then(r => r.json());
        await fetch(url, { method: 'PUT', headers: { 'Content-Type': contentType }, body: file });
    """
    bucket  = current_app.config["R2_MEDIA_BUCKET"]
    expiry  = expiry_seconds or current_app.config.get("R2_PRESIGN_EXPIRY", 3600)
    client  = _get_client()

    try:
        url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket":      bucket,
                "Key":         object_key,
                "ContentType": content_type,
            },
            ExpiresIn=expiry,
            HttpMethod="PUT",
        )
        logger.debug("Generated presigned PUT URL for %s (expires in %ds)", object_key, expiry)
        return url
    except (ClientError, BotoCoreError) as exc:
        logger.error("Failed to generate presigned PUT URL for %s: %s", object_key, exc)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA: PRESIGNED GET (private media serving)
# ─────────────────────────────────────────────────────────────────────────────

def generate_presigned_get_url(
    object_key: str,
    expiry_seconds: Optional[int] = None,
    bucket: Optional[str] = None,
) -> str:
    """
    Generate a presigned GET URL to serve a private R2 object.

    Args:
        object_key:    R2 object key.
        expiry_seconds: URL lifetime. Defaults to config value.
        bucket:        Override bucket (defaults to media bucket).

    Returns:
        Presigned GET URL string.
    """
    _bucket = bucket or current_app.config["R2_MEDIA_BUCKET"]
    expiry  = expiry_seconds or current_app.config.get("R2_PRESIGN_EXPIRY", 3600)

    try:
        url = _get_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": _bucket, "Key": object_key},
            ExpiresIn=expiry,
        )
        return url
    except (ClientError, BotoCoreError) as exc:
        logger.error("Failed to generate presigned GET URL for %s: %s", object_key, exc)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA: SMALL UPLOADS via Flask (avatars, thumbnails < 10 MB)
# ─────────────────────────────────────────────────────────────────────────────

def upload_media_bytes(
    file_bytes: bytes,
    object_key: str,
    content_type: str,
    bucket: Optional[str] = None,
) -> str:
    """
    Upload raw bytes to R2 (for small files like avatars passed through Flask).

    Args:
        file_bytes:   File content as bytes.
        object_key:   Destination R2 key, e.g. "avatars/member-42.jpg"
        content_type: MIME type.
        bucket:       Override bucket.

    Returns:
        The object_key (store this in the DB; build full URL separately).
    """
    _bucket = bucket or current_app.config["R2_MEDIA_BUCKET"]
    client  = _get_client()

    try:
        client.put_object(
            Bucket=_bucket,
            Key=object_key,
            Body=file_bytes,
            ContentType=content_type,
        )
        logger.info("Uploaded %d bytes to R2 key: %s", len(file_bytes), object_key)
        return object_key
    except (ClientError, BotoCoreError) as exc:
        logger.error("Failed to upload bytes to R2 key %s: %s", object_key, exc)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA: DELETE
# ─────────────────────────────────────────────────────────────────────────────

def delete_r2_object(object_key: str, bucket: Optional[str] = None) -> bool:
    """
    Delete a single object from R2.

    Args:
        object_key: R2 object key.
        bucket:     Override bucket.

    Returns:
        True on success.
    """
    _bucket = bucket or current_app.config["R2_MEDIA_BUCKET"]
    try:
        _get_client().delete_object(Bucket=_bucket, Key=object_key)
        logger.info("Deleted R2 object: %s from bucket %s", object_key, _bucket)
        return True
    except (ClientError, BotoCoreError) as exc:
        logger.error("Failed to delete R2 object %s: %s", object_key, exc)
        raise


def delete_r2_objects(object_keys: list[str], bucket: Optional[str] = None) -> int:
    """
    Batch-delete up to 1000 objects from R2 in a single API call.

    Args:
        object_keys: List of R2 object keys.
        bucket:      Override bucket.

    Returns:
        Number of successfully deleted objects.
    """
    _bucket = bucket or current_app.config["R2_MEDIA_BUCKET"]
    if not object_keys:
        return 0

    # R2 delete_objects accepts max 1000 per call
    deleted = 0
    for chunk in _chunked(object_keys, 1000):
        payload = {"Objects": [{"Key": k} for k in chunk], "Quiet": True}
        try:
            resp = _get_client().delete_objects(Bucket=_bucket, Delete=payload)
            deleted += len(chunk) - len(resp.get("Errors", []))
        except (ClientError, BotoCoreError) as exc:
            logger.error("Batch delete failed: %s", exc)
    return deleted


# ─────────────────────────────────────────────────────────────────────────────
# OBJECT KEY BUILDER HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def build_media_key(church_slug: str, media_type: str, filename: str) -> str:
    """
    Build a consistent, namespaced R2 object key.

    Example:
        build_media_key("cop-agona", "posts", "abc123.mp4")
        → "cop-agona/posts/abc123.mp4"
    """
    return f"{church_slug}/{media_type}/{filename}"


def build_db_key(church_slug: str) -> str:
    """
    Build the R2 object key for a tenant database file.

    Example:
        build_db_key("cop-agona-ahanta") → "cop-agona-ahanta.db"
    """
    return f"{church_slug}.db"


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _md5_of_file(path: Path) -> str:
    """Compute MD5 hex digest of a local file for ETag comparison."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _chunked(lst: list, size: int):
    """Yield successive size-chunks from lst."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]
