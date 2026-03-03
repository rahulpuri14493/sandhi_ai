from sqlalchemy import Column, Integer, String, DateTime, Text
from datetime import datetime
from db.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False)  # e.g., "job", "agent", "transaction"
    entity_id = Column(Integer, nullable=False)
    action = Column(String, nullable=False)  # e.g., "created", "updated", "executed"
    details = Column(Text)  # JSON string with additional details
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
