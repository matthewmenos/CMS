"""utils package — R2 storage, DB routing, auth helpers."""

from .r2_storage import (
    download_global_db, upload_global_db,
    download_tenant_db, upload_tenant_db,
    generate_presigned_put_url, generate_presigned_get_url,
    upload_media_bytes, delete_r2_object, delete_r2_objects,
    build_media_key, build_db_key,
    GLOBAL_DB_R2_KEY,
)
from .db_router import (
    get_tenant_engine, tenant_session,
    sync_dirty_tenants, ensure_tenant_db_schema,
    invalidate_tenant_cache, register_db_sync_hooks,
)
from .auth import (
    init_jwt, make_tokens,
    require_role, require_admin,
    get_current_member,
)

__all__ = [
    "download_global_db", "upload_global_db",
    "download_tenant_db", "upload_tenant_db",
    "generate_presigned_put_url", "generate_presigned_get_url",
    "upload_media_bytes", "delete_r2_object", "delete_r2_objects",
    "build_media_key", "build_db_key", "GLOBAL_DB_R2_KEY",
    "get_tenant_engine", "tenant_session",
    "sync_dirty_tenants", "ensure_tenant_db_schema",
    "invalidate_tenant_cache", "register_db_sync_hooks",
    "init_jwt", "make_tokens",
    "require_role", "require_admin",
    "get_current_member",
]
