from datetime import datetime

from database.db import execute_db, query_db


def notify_user(user_id, event_type, title, message, resource_type=None, resource_id=None):
    if not user_id:
        return None
    return execute_db(
        """INSERT INTO notifications
           (recipient_user_id, event_type, title, message, resource_type, resource_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, event_type, title, message, resource_type, str(resource_id) if resource_id else None),
    )


def get_user_notifications(user_id, unread_only=False, limit=50):
    where = "AND status = 'UNREAD'" if unread_only else ""
    return query_db(
        f"SELECT * FROM notifications WHERE recipient_user_id = ? {where} ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )


def mark_notification_read(notification_id, user_id):
    return execute_db(
        "UPDATE notifications SET status = 'READ', read_at = ? WHERE id = ? AND recipient_user_id = ?",
        (datetime.utcnow().isoformat(), notification_id, user_id),
    )