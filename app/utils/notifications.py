"""
Firebase Cloud Messaging (FCM) notification utility.
Sends push notifications to devices registered in tenant databases.
"""

import os
import logging
from typing import List, Optional, Dict, Any

log = logging.getLogger(__name__)

# Firebase Admin SDK
try:
    import firebase_admin
    from firebase_admin import messaging
    from firebase_admin import credentials
    
    # Initialize Firebase app if not already initialized
    if not firebase_admin._apps:
        cred_path = os.environ.get("FIREBASE_CRED_PATH", "serviceAccountKey.json")
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            log.info("Firebase Admin SDK initialized with %s", cred_path)
        else:
            log.warning("Firebase credentials file not found at %s", cred_path)
except ImportError:
    log.warning("firebase-admin not installed. Install with: pip install firebase-admin")
    firebase_admin = None
    messaging = None


def get_member_tokens(member_id: int, db_conn) -> List[str]:
    """
    Get all FCM tokens for a member from the tenant database.
    
    Args:
        member_id: The member's ID in the tenant DB
        db_conn: SQLite connection to the tenant database
    
    Returns:
        List of FCM token strings
    """
    if db_conn is None:
        return []
    
    try:
        rows = db_conn.execute(
            "SELECT fcm_token FROM device_tokens WHERE member_id = ?",
            (member_id,),
        ).fetchall()
        
        return [row["fcm_token"] for row in rows if row["fcm_token"]]
    
    except Exception as e:
        log.warning("Could not fetch device tokens: %s", e)
        return []


def notify_member(
    member_id: int,
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
    db_conn = None
) -> Dict[str, Any]:
    """
    Send a push notification to all devices registered for a member.
    
    Args:
        member_id: The member's ID in the tenant DB
        title: Notification title
        body: Notification body text
        data: Optional custom data payload for deep-linking
        db_conn: SQLite connection to the tenant database
    
    Returns:
        Dict with success count and any errors
    """
    if messaging is None:
        return {"success": 0, "error": "Firebase not configured"}
    
    tokens = get_member_tokens(member_id, db_conn)
    
    if not tokens:
        return {"success": 0, "error": "No registered devices"}
    
    # Build notification
    notification = messaging.Notification(
        title=title,
        body=body,
    )
    
    # Build data payload
    data_payload = data or {}
    
    # Create multicast message
    message = messaging.MulticastMessage(
        notification=notification,
        data=data_payload,
        tokens=tokens,
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                channel_id="church-notifications",
                click_action="FLUTTER_NOTIFICATION_CLICK",
            ),
        ),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    sound="default",
                    category="church_notification",
                ),
            ),
        ),
    )
    
    try:
        response = messaging.send_multicast(message)
        
        # Handle any failed tokens
        failed_tokens = []
        if response.failure_count > 0:
            for idx, resp in enumerate(response.responses):
                if not resp.success:
                    failed_tokens.append(tokens[idx])
                    log.warning(
                        "FCM send failed for token %s: %s",
                        tokens[idx][:20] + "...",
                        resp.exception,
                    )
        
        return {
            "success": response.success_count,
            "failure": response.failure_count,
            "failed_tokens": failed_tokens,
        }
    
    except Exception as e:
        log.error("FCM send failed: %s", e)
        return {"success": 0, "error": str(e)}


def notify_likes(post_id: int, actor_id: int, db_conn) -> None:
    """Send notification for a new like."""
    # Get post owner
    post = db_conn.execute(
        "SELECT member_id FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    
    if not post or post["member_id"] == actor_id:
        return
    
    # Get actor name
    actor = db_conn.execute(
        "SELECT display_name FROM members WHERE id = ?",
        (actor_id,),
    ).fetchone()
    
    if not actor:
        return
    
    notify_member(
        member_id=post["member_id"],
        title="New Like",
        body=f"{actor['display_name']} liked your post",
        data={
            "type": "like",
            "post_id": str(post_id),
            "actor_id": str(actor_id),
        },
        db_conn=db_conn,
    )


def notify_comments(post_id: int, actor_id: int, comment_id: int, db_conn) -> None:
    """Send notification for a new comment."""
    post = db_conn.execute(
        "SELECT member_id FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    
    if not post or post["member_id"] == actor_id:
        return
    
    actor = db_conn.execute(
        "SELECT display_name FROM members WHERE id = ?",
        (actor_id,),
    ).fetchone()
    
    if not actor:
        return
    
    notify_member(
        member_id=post["member_id"],
        title="New Comment",
        body=f"{actor['display_name']} commented on your post",
        data={
            "type": "comment",
            "post_id": str(post_id),
            "comment_id": str(comment_id),
            "actor_id": str(actor_id),
        },
        db_conn=db_conn,
    )


def notify_follows(follower_id: int, followed_id: int, db_conn) -> None:
    """Send notification for a new follow."""
    follower = db_conn.execute(
        "SELECT display_name FROM members WHERE id = ?",
        (follower_id,),
    ).fetchone()
    
    if not follower:
        return
    
    notify_member(
        member_id=followed_id,
        title="New Follower",
        body=f"{follower['display_name']} started following you",
        data={
            "type": "follow",
            "follower_id": str(follower_id),
        },
        db_conn=db_conn,
    )


def cleanup_invalid_tokens(tokens: List[str], db_conn) -> int:
    """
    Remove invalid/expired tokens from the database.
    
    Args:
        tokens: List of tokens to remove
        db_conn: SQLite connection to the tenant database
    
    Returns:
        Number of tokens removed
    """
    if not tokens:
        return 0
    
    removed = 0
    for token in tokens:
        result = db_conn.execute(
            "DELETE FROM device_tokens WHERE fcm_token = ?",
            (token,),
        )
        if result.rowcount > 0:
            removed += 1
    
    db_conn.commit()
    return removed