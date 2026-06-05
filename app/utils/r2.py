"""
Cloudflare R2 storage utility.

Two buckets:
  - MEDIA bucket : user-uploaded images/videos (public CDN URL)
  - DB bucket    : tenant SQLite .db file backups/sync

Large media uploads go direct-to-R2 via presigned PUT URLs (bypassing Flask).
DB sync and metadata ops run server-side through this module.

When R2 credentials are absent or the connection fails the module degrades
gracefully — local SQLite files are used instead.
"""

import os
import threading
import logging
import warnings
from functools import lru_cache
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointResolutionError, BotoCoreError

# Suppress the utcnow() DeprecationWarning that comes from botocore internals.
# This is a third-party library issue, not our code.
warnings.filterwarnings(
    "ignore",
    message="datetime.datetime.utcnow\\(\\) is deprecated",
    category=DeprecationWarning,
    module="botocore",
)

log = logging.getLogger(__name__)


def _r2_configured() -> bool:
    """True only when all three required R2 credentials are present."""
    return all(
        os.environ.get(k)
        for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    )


@lru_cache(maxsize=1)
def _s3_client():
    """Singleton boto3 S3 client for R2. Raises RuntimeError if not configured."""
    if not _r2_configured():
        raise RuntimeError(
            "R2 not configured — set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY in .env"
        )
    return boto3.client(
        "s3",
        endpoint_url=(
            f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
        ),
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 2, "mode": "adaptive"},
            max_pool_connections=20,
            connect_timeout=5,
            read_timeout=30,
        ),
    )


# ---------------------------------------------------------------------------
# DB SYNC
# ---------------------------------------------------------------------------

def download_tenant_db(db_key: str, local_path: str) -> bool:
    """
    Download a tenant .db file from R2 to local_path.

    Returns True on success.
    Returns False (never raises) when R2 is not configured, the object
    doesn't exist, or any network/auth error occurs — the caller will
    fall back to initialising a fresh local database.
    """
    if not _r2_configured():
        log.debug("R2 not configured — skipping download of %s", db_key)
        return False

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    try:
        _s3_client().download_file(
            Bucket=os.environ["R2_BUCKET_DB"],
            Key=db_key,
            Filename=local_path,
        )
        log.info("Downloaded tenant DB  %s → %s", db_key, local_path)
        return True

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("404", "NoSuchKey", "403", "AccessDenied"):
            log.info(
                "Tenant DB %s not found or not accessible in R2 (code=%s) — "
                "will initialise locally",
                db_key, code,
            )
        else:
            log.warning("R2 ClientError downloading %s: %s", db_key, exc)
        return False

    except (BotoCoreError, EndpointResolutionError, OSError, Exception) as exc:
        # Covers: network errors, bad endpoint, SSL failures, credential errors
        log.warning(
            "R2 download failed for %s (%s: %s) — falling back to local init",
            db_key, type(exc).__name__, exc,
        )
        return False


def upload_tenant_db(db_key: str, local_path: str) -> None:
    """Sync a local tenant .db file to R2. Silently skips if R2 not configured."""
    if not _r2_configured():
        log.debug("R2 not configured — skipping upload of %s", db_key)
        return
    try:
        _s3_client().upload_file(
            Filename=local_path,
            Bucket=os.environ["R2_BUCKET_DB"],
            Key=db_key,
            ExtraArgs={"ContentType": "application/octet-stream"},
        )
        log.info("Synced tenant DB %s → R2", db_key)
    except Exception as exc:
        log.warning("R2 upload failed for %s: %s", db_key, exc)


def upload_tenant_db_async(db_key: str, local_path: str) -> None:
    """Fire-and-forget DB sync — response is never blocked."""
    def _run():
        try:
            upload_tenant_db(db_key, local_path)
        except Exception:
            log.exception("Background DB sync failed for %s", db_key)

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# PRESIGNED UPLOAD URLS
# ---------------------------------------------------------------------------

_ALLOWED_MEDIA_TYPES = {
    "image/jpeg": ".jpg",
    "image/png":  ".png",
    "image/webp": ".webp",
    "image/gif":  ".gif",
    "video/mp4":  ".mp4",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/mp4":  ".m4a",
}

_SIZE_LIMITS = {
    "image": 10  * 1024 * 1024,   # 10 MB
    "video": 500 * 1024 * 1024,   # 500 MB
    "audio": 100 * 1024 * 1024,   # 100 MB
}


def generate_upload_presigned_url(
    object_key: str,
    content_type: str,
    expires_in: int = 3600,
) -> Optional[str]:
    """
    Return a presigned PUT URL for direct browser→R2 uploads.
    Returns None when content_type is disallowed or R2 is not configured.
    """
    if content_type not in _ALLOWED_MEDIA_TYPES:
        return None
    if not _r2_configured():
        return None
    try:
        return _s3_client().generate_presigned_url(
            "put_object",
            Params={
                "Bucket": os.environ["R2_BUCKET_MEDIA"],
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )
    except Exception as exc:
        log.warning("Could not generate presigned URL: %s", exc)
        return None


def generate_read_presigned_url(
    object_key: str,
    expires_in: int = 3600,
    bucket: Optional[str] = None,
) -> Optional[str]:
    """Presigned GET URL for private R2 objects. Returns None on failure."""
    if not _r2_configured():
        return None
    try:
        bucket = bucket or os.environ["R2_BUCKET_MEDIA"]
        return _s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": object_key},
            ExpiresIn=expires_in,
        )
    except Exception as exc:
        log.warning("Could not generate read presigned URL: %s", exc)
        return None


def public_media_url(object_key: str) -> str:
    """CDN URL for a public R2 media object, or empty string if not configured."""
    if not object_key:
        return ""
    base = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    return f"{base}/{object_key}" if base else ""


def delete_object(object_key: str, bucket: Optional[str] = None) -> None:
    """Delete one object from R2. Silently skips if R2 not configured."""
    if not _r2_configured():
        return
    try:
        b = bucket or os.environ.get("R2_BUCKET_MEDIA", "")
        _s3_client().delete_object(Bucket=b, Key=object_key)
    except Exception as exc:
        log.warning("R2 delete failed for %s: %s", object_key, exc)


def allowed_content_type(content_type: str) -> bool:
    return content_type in _ALLOWED_MEDIA_TYPES


def max_upload_size(content_type: str) -> int:
    return _SIZE_LIMITS.get(content_type.split("/")[0], 10 * 1024 * 1024)
