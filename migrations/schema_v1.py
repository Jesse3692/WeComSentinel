import time

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_ms():
    return int(time.time() * 1000)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    corp_id: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    credential_ref: Mapped[str]
    callback_key: Mapped[str] = mapped_column(unique=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[int] = mapped_column(default=now_ms)


class User(Base):
    __tablename__ = "app_users"
    __table_args__ = (
        UniqueConstraint("ding_corp_id", "ding_user_id"),
        CheckConstraint("role IN ('admin','user')"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    ding_corp_id: Mapped[str]
    ding_user_id: Mapped[str]
    name: Mapped[str]
    avatar_url: Mapped[str] = mapped_column(default="")
    role: Mapped[str] = mapped_column(default="user")
    enabled: Mapped[bool] = mapped_column(default=True)
    last_tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"))


class Grant(Base):
    __tablename__ = "user_tenants"
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id"), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)


class Session(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id"))
    token_hash: Mapped[str] = mapped_column(unique=True)
    csrf: Mapped[str]
    expires_at: Mapped[int] = mapped_column(index=True)
    revoked: Mapped[bool] = mapped_column(default=False)


class Profile(Base):
    """Tenant-scoped employee/customer/group snapshots, including member lists."""

    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    kind: Mapped[str]
    external_id: Mapped[str]
    name: Mapped[str] = mapped_column(default="未知")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    synced_at: Mapped[int] = mapped_column(default=now_ms)


class Media(Base):
    __tablename__ = "media_assets"
    __table_args__ = (UniqueConstraint("tenant_id", "sha256"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    sha256: Mapped[str]
    relative_path: Mapped[str]
    mime_type: Mapped[str]
    size_bytes: Mapped[int]


class Inbox(Base):
    __tablename__ = "callback_inbox"
    __table_args__ = (UniqueConstraint("tenant_id", "dedupe_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    dedupe_key: Mapped[str]
    payload_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    received_at: Mapped[int] = mapped_column(default=now_ms)
    status: Mapped[str] = mapped_column(default="pending")


class Job(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (Index("ix_jobs_due", "status", "next_run_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    callback_id: Mapped[int] = mapped_column(ForeignKey("callback_inbox.id"), unique=True)
    status: Mapped[str] = mapped_column(default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    next_run_at: Mapped[int] = mapped_column(default=now_ms)
    lease_until: Mapped[int | None]
    last_error: Mapped[str | None]


class BusinessEvent(Base):
    __tablename__ = "business_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "callback_id", "item_key"),
        CheckConstraint("quantity > 0"),
        Index("ix_events_time", "tenant_id", "occurred_at", "id"),
        Index("ix_events_type_time", "tenant_id", "event_type", "occurred_at"),
        Index("ix_events_employee", "tenant_id", "employee_id", "occurred_at"),
        Index("ix_events_group", "tenant_id", "group_id", "occurred_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    callback_id: Mapped[int] = mapped_column(ForeignKey("callback_inbox.id"))
    item_key: Mapped[str]
    event_type: Mapped[str]
    employee_id: Mapped[str | None]
    customer_id: Mapped[str | None]
    group_id: Mapped[str | None]
    operator_id: Mapped[str | None]
    deletion_direction: Mapped[str | None]
    occurred_at: Mapped[int] = mapped_column(Integer)
    add_way: Mapped[str | None]
    channel_state: Mapped[str | None]
    quantity: Mapped[int] = mapped_column(default=1)
    resolution: Mapped[str] = mapped_column(default="exact")
    enrichment_status: Mapped[str] = mapped_column(default="pending")
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[int] = mapped_column(default=now_ms)


class Audit(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("app_users.id"))
    action: Mapped[str]
    target: Mapped[str]
    created_at: Mapped[int] = mapped_column(default=now_ms)
