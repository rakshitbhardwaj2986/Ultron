from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any


# -------------------------------
# Pydantic Schemas (API Layer)
# -------------------------------

class MessageRequest(BaseModel):
    user_id: str
    command: str   # unified (was message before)


class AIResponse(BaseModel):
    intent: str
    response: str
    action_data: Optional[Dict[str, Any]] = None
    requires_confirmation: bool = False


class MessageResponse(BaseModel):
    user_id: str
    intent: str
    response: str
    action_data: Optional[Dict[str, Any]] = None
    action_result: Optional[str] = None   # ✅ important

    model_config = ConfigDict(from_attributes=True)


# -------------------------------
# SQLAlchemy Model (DB Layer)
# -------------------------------

class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    command: Mapped[str] = mapped_column(String(500), nullable=False)
    intent: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    response: Mapped[str] = mapped_column(String(3000), nullable=False)
    time_created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class PendingAction(Base):
    """Holds a drafted action (e.g. an unsent email) waiting on user
    confirmation. One row per user — a new draft overwrites any old one."""
    __tablename__ = "pending_actions"

    user_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    intent: Mapped[str] = mapped_column(String(100), nullable=False)
    action_data: Mapped[str] = mapped_column(String(3000), nullable=False)  # JSON string
    response: Mapped[str] = mapped_column(String(3000), nullable=True)
    time_created: Mapped[datetime] = mapped_column(default=datetime.utcnow)