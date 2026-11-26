from datetime import datetime, timezone
import random
import string
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.calculator import WeightDistributionError, calculate_weights
from app.config import settings
from app.keitaro_client import KeitaroAPIError, keitaro_client
from app.models import Campaign, OfferCache, Stream, StreamOffer


def _normalize_domain(value: str) -> str:
    cleaned = (value or "").strip().lower()
    for prefix in ("https://", "http://"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    return cleaned.rstrip("/")


def _generate_alias(length: int = 6) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choices(alphabet, k=length))


async def _load_stream(db: AsyncSession, stream_id: int) -> Optional[Stream]:
    stmt = (
        select(Stream)
        .execution_options(populate_existing=True)
        .options(selectinload(Stream.offers))
        .where(Stream.id == stream_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _load_campaign(db: AsyncSession, keitaro_id: int) -> Optional[Campaign]:
    stmt = (
        select(Campaign)
        .execution_options(populate_existing=True)
        .options(selectinload(Campaign.streams).selectinload(Stream.offers))
        .where(Campaign.keitaro_id == keitaro_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _sync_offer_cache(db: AsyncSession, remote_offers: Optional[List[Dict[str, Any]]] = None) -> List[OfferCache]:
    if remote_offers is None:
        remote_offers = await keitaro_client.get_offers()

    existing_rows = (await db.execute(select(OfferCache))).scalars().all()
    existing_map = {row.id: row for row in existing_rows}

    for ro in remote_offers:
        oid = ro.get("id")
        if oid is None:
            continue
        name = ro.get("name") or f"Offer {oid}"
        state = ro.get("state") or "active"
        if oid in existing_map:
            existing_map[oid].name = name
            existing_map[oid].state = state
        else:
            row = OfferCache(id=oid, name=name, state=state)
            db.add(row)
            existing_map[oid] = row

    await db.flush()
    return list(existing_map.values())


async def get_or_create_offer_cache(db: AsyncSession) -> List[OfferCache]:
    offers = await _sync_offer_cache(db)
    await db.commit()
    return offers


async def create_campaign_with_streams(db: AsyncSession, data: Dict[str, Any]) -> Campaign:
    name = (data.get("name") or "").strip()
    geo = (data.get("geo") or "").strip().upper()
    offer_id = int(data["offer_id"])

    if not name:
        raise HTTPException(status_code=400, detail="Campaign name must not be empty")
    if len(geo) != 2 or not geo.isascii() or not geo.isalpha():
        raise HTTPException(status_code=400, detail="Geo must be a valid 2-letter ISO country code")

    if not settings.DEFAULT_DOMAIN_NAME.strip():
        raise HTTPException(status_code=400, detail="DEFAULT_DOMAIN_NAME is not configured")
    if not settings.DEFAULT_GROUP_NAME.strip():
        raise HTTPException(status_code=400, detail="DEFAULT_GROUP_NAME is not configured")
    if not settings.DEFAULT_SOURCE_NAME.strip():
        raise HTTPException(status_code=400, detail="DEFAULT_SOURCE_NAME is not configured")

    domains = await keitaro_client.get_domains()
    groups = await keitaro_client.get_groups("campaigns")
    traffic_sources = await keitaro_client.get_traffic_sources()
    remote_offers = await keitaro_client.get_offers()

    target_domain = _normalize_domain(settings.DEFAULT_DOMAIN_NAME)
    matched_domain = next(
        (d for d in domains if _normalize_domain(d.get("name", "")) == target_domain),
        None,
    )
    if not matched_domain:
        available = [d.get("name") for d in domains]
        raise HTTPException(
            status_code=400,
            detail=f"Domain '{settings.DEFAULT_DOMAIN_NAME}' not found in Keitaro. Available: {available}",
        )

    target_group = settings.DEFAULT_GROUP_NAME.strip().lower()
    matched_group = next(
        (g for g in groups if (g.get("name") or "").strip().lower() == target_group),
        None,
    )
    if not matched_group:
        available = [g.get("name") for g in groups]
        raise HTTPException(
            status_code=400,
            detail=f"Group '{settings.DEFAULT_GROUP_NAME}' not found in Keitaro. Available: {available}",
        )

    target_source = settings.DEFAULT_SOURCE_NAME.strip().lower()
    matched_source = next(
        (s for s in traffic_sources if (s.get("name") or "").strip().lower() == target_source),
        None,
    )
    if not matched_source:
        available = [s.get("name") for s in traffic_sources]
        raise HTTPException(
            status_code=400,
            detail=f"Traffic source '{settings.DEFAULT_SOURCE_NAME}' not found in Keitaro. Available: {available}",
        )

    matched_offer = next((o for o in remote_offers if o.get("id") == offer_id), None)
    if not matched_offer:
        raise HTTPException(status_code=404, detail=f"Offer {offer_id} not found in Keitaro")

    await _sync_offer_cache(db, remote_offers)

    alias = _generate_alias()
    campaign_payload = {
        "name": name,
        "alias": alias,
        "type": "position",
        "domain_id": matched_domain["id"],
        "group_id": matched_group["id"],
        "traffic_source_id": matched_source["id"],
        "cost_type": "CPC",
        "cost_value": 0,
        "cost_currency": "USD",
        "cost_auto": True,
    }

    created_campaign = await keitaro_client.create_campaign(campaign_payload)
    created_campaign_id = created_campaign["id"]

    # Flow 1: country filter -> redirect to https://google.com
    flow_1_payload = {
        "campaign_id": created_campaign_id,
        "name": f"Geo {geo} -> Google",
        "position": 1,
        "schema": "redirect",
        "action_type": "http",
        "action_payload": "https://google.com",
        "filters": [{"name": "country", "mode": "accept", "payload": [geo]}],
        "offers": [],
    }
    try:
        await keitaro_client.create_stream(flow_1_payload)
    except KeitaroAPIError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"Campaign created, but failed to create Flow 1 (geo filter): {exc}",
                "created_campaign_id": created_campaign_id,
                "stage": "flow_1_geo",
            },
        )

    # Flow 2: main offer stream
    flow_2_payload = {
        "campaign_id": created_campaign_id,
        "name": "Main Offer",
        "position": 2,
        "schema": "landings",
        "action_type": "landings",
        "offers": [{"offer_id": offer_id, "share": 100, "state": "active"}],
    }
    try:
        await keitaro_client.create_stream(flow_2_payload)
    except KeitaroAPIError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"Campaign created, but failed to create Flow 2 (main offer): {exc}",
                "created_campaign_id": created_campaign_id,
                "stage": "flow_2_offers",
            },
        )

    return await sync_campaign_streams(
        db,
        created_campaign_id,
        discard=True,
        campaign_meta=created_campaign,
    )


async def sync_campaign_streams(
    db: AsyncSession,
    campaign_keitaro_id: int,
    discard: bool = False,
    campaign_meta: Optional[Dict[str, Any]] = None,
) -> Campaign:
    campaign = await _load_campaign(db, campaign_keitaro_id)

    if campaign and not discard:
        dirty_streams = [
            {"id": s.id, "keitaro_id": s.keitaro_id, "name": s.name}
            for s in campaign.streams
            if s.is_dirty
        ]
        if dirty_streams:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Campaign has unsaved draft changes. Confirm with discard=true to overwrite.",
                    "dirty_streams": dirty_streams,
                },
            )

    remote_streams = await keitaro_client.get_campaign_streams(campaign_keitaro_id)

    if not campaign:
        if campaign_meta is None:
            campaigns_list = await keitaro_client.get_campaigns()
            campaign_meta = next((c for c in campaigns_list if c.get("id") == campaign_keitaro_id), None)
        camp_name = (campaign_meta or {}).get("name") or f"Campaign {campaign_keitaro_id}"
        camp_alias = (campaign_meta or {}).get("alias")
        campaign = Campaign(
            keitaro_id=campaign_keitaro_id,
            name=camp_name,
            alias=camp_alias,
        )
        db.add(campaign)
        await db.flush()
    elif campaign_meta:
        campaign.name = campaign_meta.get("name", campaign.name)
        campaign.alias = campaign_meta.get("alias", campaign.alias)

    # Ensure offers_cache contains all remote offer IDs
    needed_offer_ids = {
        ro.get("offer_id")
        for rs in remote_streams
        for ro in (rs.get("offers") or [])
        if ro.get("offer_id") is not None
    }
    cache_rows = (await db.execute(select(OfferCache))).scalars().all()
    cache_map = {row.id: row.name for row in cache_rows}
    if needed_offer_ids - set(cache_map.keys()):
        refreshed_cache = await _sync_offer_cache(db)
        cache_map = {row.id: row.name for row in refreshed_cache}

    # Re-load local streams for this campaign
    local_streams = (
        await db.execute(
            select(Stream)
            .options(selectinload(Stream.offers))
            .where(Stream.campaign_id == campaign.id)
        )
    ).scalars().all()
    local_stream_by_kid = {s.keitaro_id: s for s in local_streams}

    remote_stream_kids = {rs["id"] for rs in remote_streams}
    for kid, local_s in list(local_stream_by_kid.items()):
        if kid not in remote_stream_kids:
            await db.delete(local_s)
            del local_stream_by_kid[kid]

    now_utc = datetime.now(timezone.utc)

    for rs in remote_streams:
        rkid = rs["id"]
        remote_offers = rs.get("offers") or []
        existing_stream = local_stream_by_kid.get(rkid)
        was_offer_stream = existing_stream.is_offer_stream if existing_stream else False
        is_offer_stream = (rs.get("schema") == "landings") or (len(remote_offers) > 0) or was_offer_stream

        if existing_stream:
            stream_obj = existing_stream
            stream_obj.name = rs.get("name")
            stream_obj.position = rs.get("position", 1)
            stream_obj.is_offer_stream = is_offer_stream
            stream_obj.is_dirty = False
            stream_obj.raw_payload = rs
            stream_obj.synced_at = now_utc
        else:
            stream_obj = Stream(
                keitaro_id=rkid,
                campaign_id=campaign.id,
                name=rs.get("name"),
                position=rs.get("position", 1),
                is_offer_stream=is_offer_stream,
                is_dirty=False,
                raw_payload=rs,
                synced_at=now_utc,
            )
            db.add(stream_obj)
            await db.flush()

        if is_offer_stream:
            existing_offers = (
                await db.execute(
                    select(StreamOffer)
                    .where(StreamOffer.stream_id == stream_obj.id)
                    .order_by(StreamOffer.sort_order, StreamOffer.id)
                )
            ).scalars().all()

            kept_by_offer_id: Dict[int, StreamOffer] = {}
            for so in existing_offers:
                if so.committed_status is None:
                    await db.delete(so)
                else:
                    kept_by_offer_id[so.offer_id] = so

            max_order = max([so.sort_order for so in kept_by_offer_id.values()], default=-1)
            remote_offer_ids = set()

            for ro in remote_offers:
                oid = ro["offer_id"]
                remote_offer_ids.add(oid)
                share = int(ro.get("share", 0))
                oname = cache_map.get(oid, f"Offer {oid}")

                if oid in kept_by_offer_id:
                    so = kept_by_offer_id[oid]
                    so.offer_name = oname
                    so.share = share
                    so.status = "active"
                    so.committed_share = share
                    so.committed_status = "active"
                    so.committed_is_pinned = so.is_pinned
                else:
                    max_order += 1
                    db.add(
                        StreamOffer(
                            stream_id=stream_obj.id,
                            offer_id=oid,
                            offer_name=oname,
                            sort_order=max_order,
                            share=share,
                            is_pinned=False,
                            status="active",
                            committed_share=share,
                            committed_status="active",
                            committed_is_pinned=False,
                        )
                    )

            # Detect offers removed externally in Keitaro
            for oid, so in kept_by_offer_id.items():
                if oid not in remote_offer_ids:
                    so.status = "removed"
                    so.share = 0
                    so.is_pinned = False
                    so.committed_status = "removed"
                    so.committed_share = 0
                    so.committed_is_pinned = False

    await db.commit()
    loaded = await _load_campaign(db, campaign_keitaro_id)
    assert loaded is not None
    return loaded


async def _get_ordered_stream_offers(db: AsyncSession, stream_id: int) -> List[StreamOffer]:
    stmt = (
        select(StreamOffer)
        .where(StreamOffer.stream_id == stream_id)
        .order_by(StreamOffer.sort_order, StreamOffer.id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def add_offer_to_stream(db: AsyncSession, stream_id: int, offer_id: int) -> Stream:
    stream = await db.get(Stream, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    if not stream.is_offer_stream:
        raise HTTPException(status_code=400, detail="Cannot add offers to non-offer streams")

    cache_offer = await db.get(OfferCache, offer_id)
    if not cache_offer:
        await _sync_offer_cache(db)
        cache_offer = await db.get(OfferCache, offer_id)
        if not cache_offer:
            await db.rollback()
            raise HTTPException(status_code=404, detail="Offer not found in Keitaro")

    stmt = select(StreamOffer).where(StreamOffer.stream_id == stream.id, StreamOffer.offer_id == offer_id)
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing:
        if existing.status in ("active", "draft_added"):
            await db.rollback()
            raise HTTPException(status_code=400, detail="Offer is already active in stream")
        existing.status = "draft_added" if existing.committed_status is None else "active"
        existing.is_pinned = False
    else:
        all_offers = await _get_ordered_stream_offers(db, stream.id)
        max_order = max([o.sort_order for o in all_offers], default=-1)
        db.add(
            StreamOffer(
                stream_id=stream.id,
                offer_id=offer_id,
                offer_name=cache_offer.name,
                sort_order=max_order + 1,
                share=0,
                is_pinned=False,
                status="draft_added",
                committed_status=None,
                committed_share=None,
                committed_is_pinned=False,
            )
        )

    await db.flush()
    current_offers = await _get_ordered_stream_offers(db, stream.id)

    try:
        calculate_weights(current_offers)
    except WeightDistributionError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    stream.is_dirty = True
    await db.commit()
    loaded = await _load_stream(db, stream.id)
    assert loaded is not None
    return loaded


async def remove_offer_from_stream(db: AsyncSession, stream_id: int, offer_id: int) -> Stream:
    stream = await db.get(Stream, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    if not stream.is_offer_stream:
        raise HTTPException(status_code=400, detail="Cannot modify non-offer streams")

    stmt = select(StreamOffer).where(StreamOffer.stream_id == stream.id, StreamOffer.offer_id == offer_id)
    offer = (await db.execute(stmt)).scalar_one_or_none()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found in stream")

    offer.status = "removed"
    offer.share = 0
    offer.is_pinned = False

    current_offers = await _get_ordered_stream_offers(db, stream.id)

    try:
        calculate_weights(current_offers)
    except WeightDistributionError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    stream.is_dirty = True
    await db.commit()
    loaded = await _load_stream(db, stream.id)
    assert loaded is not None
    return loaded


async def bring_back_offer(db: AsyncSession, stream_id: int, offer_id: int) -> Stream:
    stream = await db.get(Stream, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    if not stream.is_offer_stream:
        raise HTTPException(status_code=400, detail="Cannot modify non-offer streams")

    stmt = select(StreamOffer).where(StreamOffer.stream_id == stream.id, StreamOffer.offer_id == offer_id)
    offer = (await db.execute(stmt)).scalar_one_or_none()
    if not offer or offer.status != "removed":
        raise HTTPException(status_code=404, detail="Removed offer not found in stream")

    offer.status = "draft_added" if offer.committed_status is None else "active"
    offer.is_pinned = False

    current_offers = await _get_ordered_stream_offers(db, stream.id)

    try:
        calculate_weights(current_offers)
    except WeightDistributionError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    stream.is_dirty = True
    await db.commit()
    loaded = await _load_stream(db, stream.id)
    assert loaded is not None
    return loaded


async def pin_offer(
    db: AsyncSession,
    stream_id: int,
    offer_id: int,
    is_pinned: bool,
    share: Optional[int],
) -> Stream:
    stream = await db.get(Stream, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    if not stream.is_offer_stream:
        raise HTTPException(status_code=400, detail="Cannot modify non-offer streams")

    stmt = select(StreamOffer).where(StreamOffer.stream_id == stream.id, StreamOffer.offer_id == offer_id)
    offer = (await db.execute(stmt)).scalar_one_or_none()
    if not offer or offer.status == "removed":
        raise HTTPException(status_code=400, detail="Cannot pin removed offer")

    offer.is_pinned = is_pinned
    if is_pinned and share is not None:
        if not (0 <= share <= 100):
            await db.rollback()
            raise HTTPException(status_code=400, detail="Share must be between 0 and 100")
        offer.share = share

    current_offers = await _get_ordered_stream_offers(db, stream.id)

    try:
        calculate_weights(current_offers)
    except WeightDistributionError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    stream.is_dirty = True
    await db.commit()
    loaded = await _load_stream(db, stream.id)
    assert loaded is not None
    return loaded


async def cancel_stream_draft(db: AsyncSession, stream_id: int) -> Stream:
    stream = await db.get(Stream, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    offers = await _get_ordered_stream_offers(db, stream.id)

    for o in offers:
        if o.committed_status is None:
            await db.delete(o)
        else:
            o.status = o.committed_status
            o.share = o.committed_share if o.committed_share is not None else 0
            o.is_pinned = o.committed_is_pinned

    stream.is_dirty = False
    await db.commit()
    loaded = await _load_stream(db, stream.id)
    assert loaded is not None
    return loaded


async def push_stream_to_keitaro(db: AsyncSession, stream_id: int, force: bool = False) -> Stream:
    stream = await _load_stream(db, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    if not stream.is_offer_stream:
        raise HTTPException(status_code=400, detail="Cannot push non-offer streams")

    active_offers = [o for o in stream.offers if o.status in ("active", "draft_added")]
    if not active_offers:
        raise HTTPException(status_code=400, detail="Нельзя опубликовать пустой поток без офферов")

    total_share = sum(o.share for o in active_offers)
    if total_share != 100:
        raise HTTPException(status_code=400, detail=f"Total offers share must be 100%, got {total_share}%")

    # Fetch remote stream for optimistic locking & preserving settings
    remote_stream = await keitaro_client.get_stream(stream.keitaro_id)

    if not force:
        remote_offers = remote_stream.get("offers") or []
        remote_snapshot = {
            (o.get("offer_id"), int(o.get("share", 0)), o.get("state", "active"))
            for o in remote_offers
        }
        committed_snapshot = {
            (o.offer_id, o.committed_share or 0, "active")
            for o in stream.offers
            if o.committed_status == "active"
        }
        if remote_snapshot != committed_snapshot:
            raise HTTPException(
                status_code=409,
                detail="Поток был изменён в Keitaro с момента последнего Fetch Streams",
            )

    new_offers_payload = [
        {"offer_id": o.offer_id, "share": o.share, "state": "active"}
        for o in active_offers
    ]

    update_payload = dict(remote_stream) if isinstance(remote_stream, dict) else (dict(stream.raw_payload) if stream.raw_payload else {})
    update_payload["offers"] = new_offers_payload

    updated_remote = await keitaro_client.update_stream(stream.keitaro_id, update_payload)

    for o in list(stream.offers):
        if o.status == "removed" and o.committed_status is None:
            await db.delete(o)
        else:
            if o.status == "draft_added":
                o.status = "active"
            o.committed_share = o.share
            o.committed_status = o.status
            o.committed_is_pinned = o.is_pinned

    stream.raw_payload = updated_remote
    stream.synced_at = datetime.now(timezone.utc)
    stream.is_dirty = False

    await db.commit()
    loaded = await _load_stream(db, stream.id)
    assert loaded is not None
    return loaded
