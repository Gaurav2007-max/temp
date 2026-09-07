from flask import request
from database.db import get_db, execute_db, query_db

def log_audit_event(action, resource_type, resource_id=None, details=None, actor=None):
    """
    Appends an immutable audit log entry for security and compliance tracking.
    """
    actor_id = None
    actor_name = "System"
    actor_role = "system"

    if actor:
        try:
            # Handle sqlite3.Row, dict, or custom mapping
            actor_id = actor["id"]
            actor_name = actor["name"] if "name" in actor.keys() else actor["username"]
            actor_role = actor["role"]
        except Exception:
            actor_id = getattr(actor, "id", None)
            actor_name = getattr(actor, "name", getattr(actor, "username", "System"))
            actor_role = getattr(actor, "role", "system")
    else:
        try:
            from flask import session
            if "user_id" in session:
                actor_id = session.get("user_id")
                actor_name = session.get("user_name", "System")
                actor_role = session.get("user_role", "system")
        except Exception:
            pass

    ip_address = None
    try:
        if request:
            ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
    except Exception:
        pass

    execute_db(
        """
        INSERT INTO audit_logs (
            actor_id, actor_name, actor_role, action, resource_type, resource_id, details, ip_address
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (actor_id, actor_name, actor_role, action, resource_type, str(resource_id) if resource_id else None, details, ip_address)
    )

def get_recent_audit_logs(limit=50):
    return query_db(
        "SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?",
        (limit,)
    )
