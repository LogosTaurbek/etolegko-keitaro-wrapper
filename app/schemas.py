from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class CampaignCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    geo: str = Field(..., min_length=2, max_length=2)
    offer_id: int

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Campaign name must not be empty")
        return cleaned

    @field_validator("geo")
    @classmethod
    def validate_geo(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if len(cleaned) != 2 or not cleaned.isascii() or not cleaned.isalpha():
            raise ValueError("Geo must be a 2-letter ISO country code")
        return cleaned


class OfferItem(BaseModel):
    id: int
    offer_id: int
    offer_name: str
    sort_order: int
    share: int
    is_pinned: bool
    status: str
    committed_share: Optional[int] = None
    committed_status: Optional[str] = None
    committed_is_pinned: bool = False


class StreamOut(BaseModel):
    id: int
    keitaro_id: int
    name: Optional[str] = None
    position: int
    is_offer_stream: bool
    has_offers: bool = False
    is_dirty: bool
    share_total: int = 0
    synced_at: Optional[str] = None
    offers: List[OfferItem] = []


class CampaignOut(BaseModel):
    id: int
    keitaro_id: int
    name: str
    alias: Optional[str] = None
    streams: List[StreamOut] = []


class OfferAddRequest(BaseModel):
    offer_id: int


class PinRequest(BaseModel):
    is_pinned: bool
    share: Optional[int] = None
