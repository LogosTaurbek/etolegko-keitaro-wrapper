from datetime import datetime
from typing import List, Optional
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.database import Base

class OfferCache(Base):
    __tablename__ = "offers_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), default="active")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keitaro_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    alias: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), onupdate=func.now())

    streams: Mapped[List["Stream"]] = relationship(
        "Stream",
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="Stream.position"
    )

class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keitaro_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_offer_stream: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="streams")
    offers: Mapped[List["StreamOffer"]] = relationship(
        "StreamOffer",
        back_populates="stream",
        cascade="all, delete-orphan",
        order_by=lambda: [StreamOffer.sort_order, StreamOffer.id]
    )

class StreamOffer(Base):
    __tablename__ = "stream_offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id", ondelete="CASCADE"), nullable=False, index=True)
    offer_id: Mapped[int] = mapped_column(Integer, nullable=False)
    offer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    share: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    committed_share: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    committed_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    committed_is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    stream: Mapped["Stream"] = relationship("Stream", back_populates="offers")

    __table_args__ = (
        UniqueConstraint("stream_id", "offer_id", name="uq_stream_offer"),
        CheckConstraint("share >= 0 AND share <= 100", name="ck_stream_offer_share_range"),
        CheckConstraint("status IN ('active', 'draft_added', 'removed')", name="ck_stream_offer_status"),
    )
