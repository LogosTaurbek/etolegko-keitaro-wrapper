from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.keitaro_client import keitaro_client
from app.models import Campaign, OfferCache, Stream
from app.schemas import (
    CampaignCreateRequest,
    CampaignOut,
    OfferAddRequest,
    OfferItem,
    PinRequest,
    StreamOut,
)
from app.services import (
    add_offer_to_stream,
    bring_back_offer,
    cancel_stream_draft,
    create_campaign_with_streams,
    get_or_create_offer_cache,
    pin_offer,
    push_stream_to_keitaro,
    remove_offer_from_stream,
    sync_campaign_streams,
)

router = APIRouter(prefix="/api")


@router.get("/offers/search", response_model=List[dict])
async def search_offers(q: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    total_cached = (await db.execute(select(func.count()).select_from(OfferCache))).scalar_one()
    if total_cached == 0:
        await get_or_create_offer_cache(db)

    query_text = (q or "").strip()
    if query_text:
        conditions = [OfferCache.name.ilike(f"%{query_text}%")]
        if query_text.isdigit():
            conditions.append(OfferCache.id == int(query_text))
        stmt = select(OfferCache).where(or_(*conditions)).order_by(OfferCache.id).limit(50)
    else:
        stmt = select(OfferCache).order_by(OfferCache.id).limit(50)

    offers = (await db.execute(stmt)).scalars().all()
    return [{"id": o.id, "name": o.name, "state": o.state} for o in offers]


@router.post("/offers/sync")
async def sync_offers(db: AsyncSession = Depends(get_db)):
    offers = await get_or_create_offer_cache(db)
    return {"status": "success", "count": len(offers)}


@router.get("/campaigns", response_model=List[dict])
async def list_campaigns():
    return await keitaro_client.get_campaigns()


@router.post("/campaigns", response_model=CampaignOut)
async def create_campaign(payload: CampaignCreateRequest, db: AsyncSession = Depends(get_db)):
    campaign = await create_campaign_with_streams(db, payload.model_dump())
    return format_campaign_out(campaign)


@router.post("/campaigns/{campaign_keitaro_id}/fetch-streams", response_model=CampaignOut)
async def fetch_campaign_streams(
    campaign_keitaro_id: int,
    discard: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    campaign = await sync_campaign_streams(db, campaign_keitaro_id, discard=discard)
    return format_campaign_out(campaign)


@router.get("/campaigns/{campaign_keitaro_id}/streams", response_model=List[StreamOut])
async def get_campaign_streams_local(campaign_keitaro_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Campaign).where(Campaign.keitaro_id == campaign_keitaro_id)
    campaign = (await db.execute(stmt)).scalar_one_or_none()
    if not campaign:
        return []

    stmt_streams = (
        select(Stream)
        .options(selectinload(Stream.offers))
        .where(Stream.campaign_id == campaign.id)
        .order_by(Stream.position, Stream.id)
    )
    streams = (await db.execute(stmt_streams)).scalars().all()
    return [format_stream_out(s) for s in streams]


@router.post("/streams/{stream_id}/offers", response_model=StreamOut)
async def add_offer(stream_id: int, payload: OfferAddRequest, db: AsyncSession = Depends(get_db)):
    stream = await add_offer_to_stream(db, stream_id, payload.offer_id)
    return format_stream_out(stream)


@router.post("/streams/{stream_id}/offers/{offer_id}/remove", response_model=StreamOut)
async def remove_offer(stream_id: int, offer_id: int, db: AsyncSession = Depends(get_db)):
    stream = await remove_offer_from_stream(db, stream_id, offer_id)
    return format_stream_out(stream)


@router.post("/streams/{stream_id}/offers/{offer_id}/bring-back", response_model=StreamOut)
async def bring_back(stream_id: int, offer_id: int, db: AsyncSession = Depends(get_db)):
    stream = await bring_back_offer(db, stream_id, offer_id)
    return format_stream_out(stream)


@router.post("/streams/{stream_id}/offers/{offer_id}/pin", response_model=StreamOut)
async def pin_stream_offer(
    stream_id: int,
    offer_id: int,
    payload: PinRequest,
    db: AsyncSession = Depends(get_db),
):
    stream = await pin_offer(db, stream_id, offer_id, payload.is_pinned, payload.share)
    return format_stream_out(stream)


@router.post("/streams/{stream_id}/push", response_model=StreamOut)
async def push_stream(
    stream_id: int,
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    stream = await push_stream_to_keitaro(db, stream_id, force=force)
    return format_stream_out(stream)


@router.post("/streams/{stream_id}/cancel", response_model=StreamOut)
async def cancel_stream(stream_id: int, db: AsyncSession = Depends(get_db)):
    stream = await cancel_stream_draft(db, stream_id)
    return format_stream_out(stream)


def format_stream_out(stream: Stream) -> StreamOut:
    ordered_offers = sorted(stream.offers, key=lambda o: (o.sort_order, o.id))
    offers = [
        OfferItem(
            id=o.id,
            offer_id=o.offer_id,
            offer_name=o.offer_name,
            sort_order=o.sort_order,
            share=o.share,
            is_pinned=o.is_pinned,
            status=o.status,
            committed_share=o.committed_share,
            committed_status=o.committed_status,
            committed_is_pinned=o.committed_is_pinned,
        )
        for o in ordered_offers
    ]
    active_offers = [o for o in offers if o.status in ("active", "draft_added")]
    share_total = sum(o.share for o in active_offers)
    return StreamOut(
        id=stream.id,
        keitaro_id=stream.keitaro_id,
        name=stream.name,
        position=stream.position,
        is_offer_stream=stream.is_offer_stream,
        has_offers=len(active_offers) > 0,
        is_dirty=stream.is_dirty,
        share_total=share_total,
        synced_at=stream.synced_at.isoformat() if stream.synced_at else None,
        offers=offers,
    )


def format_campaign_out(campaign: Campaign) -> CampaignOut:
    ordered_streams = sorted(campaign.streams, key=lambda s: (s.position, s.id))
    return CampaignOut(
        id=campaign.id,
        keitaro_id=campaign.keitaro_id,
        name=campaign.name,
        alias=campaign.alias,
        streams=[format_stream_out(s) for s in ordered_streams],
    )
