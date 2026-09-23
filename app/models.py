from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

SIZES = ("wish", "task", "project")
CATEGORIES = ("urgent", "enabling", "improvement", "wish")
STATUSES = ("proposed", "idea", "researching", "quoting", "approved", "scheduled", "in_progress", "done", "parked", "archived")
SOURCES = ("manual", "claude", "survey")
QUOTE_STATUSES = ("requested", "received", "accepted", "declined", "expired")
ATTACHMENT_KINDS = ("photo", "receipt", "quote_pdf", "certificate", "other")


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Audited:
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class User(Audited, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(256))


class Room(Audited, Base):
    __tablename__ = "rooms"
    name: Mapped[str] = mapped_column(String(128), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Phase(Audited, Base):
    __tablename__ = "phases"
    name: Mapped[str] = mapped_column(String(128), unique=True)
    budget_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Item(Audited, Base):
    __tablename__ = "items"
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    size: Mapped[str] = mapped_column(String(16), default="wish")
    category: Mapped[str] = mapped_column(String(16), default="wish")
    status: Mapped[str] = mapped_column(String(16), default="idea")
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id"), nullable=True)
    phase_id: Mapped[int | None] = mapped_column(ForeignKey("phases.id"), nullable=True)
    estimate_low: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimate_high: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_cost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    planned_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    needs_building_control: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_planning_check: Mapped[bool] = mapped_column(Boolean, default=False)
    compliance_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_group: Mapped[str | None] = mapped_column(String(200), nullable=True)

    room: Mapped[Room | None] = relationship(lazy="joined")
    phase: Mapped[Phase | None] = relationship(lazy="joined")
    quotes: Mapped[list["Quote"]] = relationship(back_populates="item", order_by="Quote.amount")
    notes: Mapped[list["Note"]] = relationship(order_by="Note.created_at")
    attachments: Mapped[list["Attachment"]] = relationship(foreign_keys="Attachment.item_id")


class Dependency(Audited, Base):
    __tablename__ = "dependencies"
    __table_args__ = (UniqueConstraint("blocker_item_id", "blocked_item_id"),)
    blocker_item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    blocked_item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))


class Contractor(Audited, Base):
    __tablename__ = "contractors"
    name: Mapped[str] = mapped_column(String(200))
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trades: Mapped[str | None] = mapped_column(String(300), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    website: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recommended_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    registrations: Mapped[str | None] = mapped_column(Text, nullable=True)
    insurance_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Quote(Audited, Base):
    __tablename__ = "quotes"
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id"), nullable=True)
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    includes_vat: Mapped[bool] = mapped_column(Boolean, default=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    exclusions: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="requested")
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id", use_alter=True), nullable=True)

    item: Mapped[Item] = relationship(back_populates="quotes")
    contractor: Mapped[Contractor | None] = relationship(lazy="joined")


class Attachment(Audited, Base):
    __tablename__ = "attachments"
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"), nullable=True)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="photo")
    filename: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)


class Note(Audited, Base):
    __tablename__ = "notes"
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    body: Mapped[str] = mapped_column(Text)


class SavingsEntry(Audited, Base):
    __tablename__ = "savings_entries"
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class ActivityLog(Base):
    __tablename__ = "activity_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    via: Mapped[str] = mapped_column(String(16))
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(32))
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=now)
    undone: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User | None] = relationship(lazy="joined")


class OAuthClient(Base):
    __tablename__ = "oauth_clients"
    client_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_info_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class OAuthCode(Base):
    __tablename__ = "oauth_codes"
    code_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    data_json: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"
    token_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    client_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    scopes: Mapped[str] = mapped_column(Text, default="")
    resource: Mapped[str | None] = mapped_column(String(300), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
