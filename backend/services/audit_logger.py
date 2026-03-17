from sqlalchemy.orm import Session
from typing import Dict, Any
import json
from models.audit_log import AuditLog


class AuditLogger:
    def __init__(self, db: Session):
        self.db = db

    def log(
        self,
        entity_type: str,
        entity_id: int,
        action: str,
        details: Dict[str, Any] = None,
    ):
        """Log an action to the audit log"""
        log_entry = AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            details=json.dumps(details) if details else None,
        )
        self.db.add(log_entry)
        self.db.commit()
