"""Customers and internal staff — deliberately separate tables. See docs/schema.md."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ARRAY, DateTime, ForeignKey, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AdminPermission, Base, pg_enum


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    name: Mapped[str] = mapped_column(Text)
    # Uniqueness is on lower(email), enforced by a functional index the migration owns.
    # Every lookup must be written `WHERE lower(email) = lower(:email)`.
    email: Mapped[str] = mapped_column(Text)
    school: Mapped[str | None] = mapped_column(Text)
    google_sub: Mapped[str | None] = mapped_column(Text)


class AdminAccount(Base):
    __tablename__ = "admin_accounts"

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    google_sub: Mapped[str | None] = mapped_column(Text)
    permissions: Mapped[list[AdminPermission]] = mapped_column(
        ARRAY(pg_enum(AdminPermission, "admin_permission")), server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_accounts.id", ondelete="RESTRICT")
    )
    # Accounts are deactivated, never deleted — audit columns across the schema reference them.
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
