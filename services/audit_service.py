"""Audit logging service for tracking all lifecycle transitions and officer actions."""
import json
from database.db import get_db, utc_now_iso

class AuditService:
    @staticmethod
    def log(user_id, user_role, action, entity_type, entity_id, details=None):
        """Append an event to the immutable audit log."""
        conn = get_db()
        cursor = conn.cursor()
        now_iso = utc_now_iso()
        details_str = json.dumps(details) if isinstance(details, (dict, list)) else (details or "{}")

        cursor.execute("""
        INSERT INTO audit_logs (user_id, user_role, action, entity_type, entity_id, details_json, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, user_role, action, entity_type, entity_id, details_str, now_iso))
        
        log_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return log_id

    @staticmethod
    def get_logs_for_tender(tender_id, limit=50):
        """Get audit trail for a specific tender."""
        return AuditService.query_logs(tender_id=tender_id, limit=limit)

    @staticmethod
    def query_logs(tender_id=None, submission_id=None, event_type=None, start_date=None, end_date=None, limit=100):
        """Query immutable audit trail by tender, submission, event type, and date range."""
        conn = get_db()
        cursor = conn.cursor()

        query = """
        SELECT a.*, u.username, u.full_name
        FROM audit_logs a
        LEFT JOIN users u ON a.user_id = u.id
        WHERE 1=1
        """
        params = []

        if tender_id:
            query += ' AND ((a.entity_type = "tender" AND a.entity_id = ?) OR a.details_json LIKE ?)'
            params.extend([tender_id, f'%"tender_id": {tender_id}%'])

        if submission_id:
            query += ' AND ((a.entity_type = "submission" AND a.entity_id = ?) OR a.details_json LIKE ?)'
            params.extend([submission_id, f'%"submission_id": {submission_id}%'])

        if event_type:
            query += ' AND a.action = ?'
            params.append(event_type)

        if start_date:
            query += ' AND a.timestamp >= ?'
            params.append(start_date)

        if end_date:
            query += ' AND a.timestamp <= ?'
            params.append(end_date)

        query += " ORDER BY a.id DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, tuple(params))
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    @staticmethod
    def export_logs_json(tender_id=None):
        """Export audit trail in structured JSON format."""
        logs = AuditService.query_logs(tender_id=tender_id, limit=5000)
        return json.dumps(logs, indent=2)

    @staticmethod
    def export_logs_csv(tender_id=None):
        """Export audit trail in RFC 4180 compliant CSV format."""
        import io
        import csv
        logs = AuditService.query_logs(tender_id=tender_id, limit=5000)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Timestamp_UTC", "Actor_ID", "Actor_Username", "Actor_Role", "Action_Event", "Entity_Type", "Entity_ID", "Details_JSON"])
        for r in logs:
            writer.writerow([
                r.get("id"),
                r.get("timestamp"),
                r.get("user_id"),
                r.get("username") or "system",
                r.get("user_role") or "system",
                r.get("action"),
                r.get("entity_type"),
                r.get("entity_id"),
                r.get("details_json")
            ])
        return output.getvalue()
