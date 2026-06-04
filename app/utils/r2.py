"""
Cloudflare R2 storage utility.

Two buckets:
  - MEDIA bucket : user-uploaded images/videos (served publicly via R2 public URL)
  - DB bucket    : tenant SQLite .db file backups/sync

All heavy media uploads go direct-to-R2 via presigned PUT URLs (bypassing Flask).
Only .db sync and metadata ops go through this module on the server side.
"""

import os
import threading
import logging
from functools import lru_cache
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

_lock = threading.Lock()


@lru_cache(maxsize=1)
def _s3_client():
    """Singleton boto3 S3 client configured for R2."""
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "adaptive"},
            max_pool_connections=20,
        ),
    )


# ---------------------------------------------------------------------------
# DB SYNC  (tenant SQLite files)
# ---------------------------------------------------------------------------

def download_tenant_db(db_key: str, local_path: str) -> bool:
    """
    Download a tenant .db file from R2 to local_path.
    Returns True on success, False if the object does not exist yet.
    """
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    try:
        _s3_client().download_file(
            Bucket=os.environ["R2_BUCKET_DB"],
            Key=db_key,
            Filename=local_path,
        )
        log.info("Downloaded tenant DB %s → %s", db_key, local_path)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            log.info("Tenant DB %s not found in R2 (new tenant)", db_key)
            return False
        log.exception("R2 download error for %s", db_key)
        raise


def upload_tenant_db(db_key: str, local_path: str) -> None:
    """Upload (sync) the local tenant .db file back to R2."""
    _s3_client().upload_file(
        Filename=local_path,
        Bucket=os.environ["R2_BUCKET_DB"],
        Key=db_key,
        ExtraArgs={"ContentType": "application/octet-stream"},
    )
    log.info("Synced tenant DB %s → R2", db_key)


def upload_tenant_db_async(db_key: str, local_path: str) -> None:
    """Fire-and-forget DB sync so the response isn't blocked."""
    def _run():
        try:
            upload_tenant_db(db_key, local_path)
        except Exception:
            log.exception("Background DB sync failed for %s", db_key)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# PRESIGNED URLS  (direct-to-R2 media uploads from the browser)
# ---------------------------------------------------------------------------

_ALLOWED_MEDIA_TYPES = {
    "image/jpeg":  ".jpg",
    "image/png":   ".png",
    "image/webp":  ".webp",
    "image/gif":   ".gif",
    "video/mp4":   ".mp4",
    "video/webm":  ".webm",
    "audio/mpeg":  ".mp3",
    "audio/mp4":   ".m4a",
}

_SIZE_LIMITS = {
    "image": 10 * 1024 * 1024,    # 10 MB
    "video": 500 * 1024 * 1024,   # 500 MB
    "audio": 100 * 1024 * 1024,   # 100 MB
}


def generate_upload_presigned_url(
    object_key: str,
    content_type: str,
    expires_in: int = 3600,
) -> Optional[str]:
    """
    Generate a presigned PUT URL so the browser can upload directly to R2.
    Returns None if the content_type is not in the allowed list.
    """
    if content_type not in _ALLOWED_MEDIA_TYPES:
        return None

    url = _s3_client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": os.environ["R2_BUCKET_MEDIA"],
            "Key": object_key,
            "ContentType": content_type,
        },
        ExpiresIn=expires_in,
        HttpMethod="PUT",
    )
    return url


def generate_read_presigned_url(
    object_key: str,
    expires_in: int = 3600,
    bucket: Optional[str] = None,
) -> str:
    """Generate a presigned GET URL for private R2 objects."""
    bucket = bucket or os.environ["R2_BUCKET_MEDIA"]
    return _s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": object_key},
        ExpiresIn=expires_in,
    )


def public_media_url(object_key: str) -> str:
    """Return the public CDN URL for an R2 media object."""
    base = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    return f"{base}/{object_key}"


def delete_object(object_key: str, bucket: Optional[str] = None) -> None:
    """Delete a single object from R2."""
    bucket = bucket or os.environ["R2_BUCKET_MEDIA"]
    try:
        _s3_client().delete_object(Bucket=bucket, Key=object_key)
    except ClientError:
        log.exception("Failed to delete R2 object %s", object_key)


def allowed_content_type(content_type: str) -> bool:
    return content_type in _ALLOWED_MEDIA_TYPES


def max_upload_size(content_type: str) -> int:
    kind = content_type.split("/")[0]
    return _SIZE_LIMITS.get(kind, 10 * 1024 * 1024)
