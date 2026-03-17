from sqlalchemy import Column, Integer, String, DateTime, Text
from datetime import datetime
from db.database import Base


class AuditLog(Base):
    """
    Represents an audit log entry in the database.

    Attributes:
        id (int): Unique identifier for the audit log entry.
        entity_type (str): Type of entity being audited (e.g., "job", "agent", "transaction").
        entity_id (int): Identifier for the audited entity.
        action (str): Type of action being audited (e.g., "created", "updated", "executed").
        details (str): JSON string with additional details about the audit log entry.
        timestamp (datetime): Timestamp when the audit log entry was created.
    """

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False)  # e.g., "job", "agent", "transaction"
    entity_id = Column(Integer, nullable=False)
    action = Column(String, nullable=False)  # e.g., "created", "updated", "executed"
    details = Column(Text)  # JSON string with additional details
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)