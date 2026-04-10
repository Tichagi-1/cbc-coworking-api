from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Booking, Resource, ResourceType, Tenant, User
from app.core.auth import get_current_user

router = APIRouter(prefix="/workspace", tags=["workspace"])

PALETTE = [
    "#003DA5", "#7C3AED", "#059669", "#D97706", "#DC2626",
    "#0891B2", "#4F46E5", "#B45309", "#0E7490", "#9333EA",
]


@router.get("/timeline")
async def workspace_timeline(
    building_id: int = 1,
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        start_dt = datetime.now()
        end_dt = start_dt + timedelta(days=14)

    # Fetch resources
    res_result = await db.execute(
        select(Resource).where(Resource.building_id == building_id)
    )
    resources = res_result.scalars().all()

    # Build tenant→color map
    tenant_colors: dict[str, str] = {}
    color_idx = 0

    events = []

    for r in resources:
        # Occupancy events for occupied resources
        if r.status and r.status.value == "occupied" and r.tenant_name:
            if r.tenant_name not in tenant_colors:
                tenant_colors[r.tenant_name] = PALETTE[color_idx % len(PALETTE)]
                color_idx += 1
            events.append({
                "id": f"occupancy-{r.id}",
                "resource_id": r.id,
                "resource_name": r.name,
                "resource_type": r.resource_type.value if r.resource_type else "office",
                "title": r.tenant_name,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "event_type": "occupancy",
                "color": tenant_colors[r.tenant_name],
            })

    # Bookings in date range
    bookings_result = await db.execute(
        select(Booking).where(
            Booking.start_time < end_dt,
            Booking.end_time > start_dt,
        )
    )
    bookings = bookings_result.scalars().all()
    res_by_id = {r.id: r for r in resources}

    for b in bookings:
        if b.resource_id not in res_by_id:
            continue
        r = res_by_id[b.resource_id]
        # Get tenant name
        tenant = await db.get(Tenant, b.tenant_id) if b.tenant_id else None
        t_name = tenant.company_name if tenant else "Booking"
        if t_name not in tenant_colors:
            tenant_colors[t_name] = PALETTE[color_idx % len(PALETTE)]
            color_idx += 1
        events.append({
            "id": f"booking-{b.id}",
            "resource_id": b.resource_id,
            "resource_name": r.name,
            "resource_type": r.resource_type.value if r.resource_type else "meeting_room",
            "title": f"{t_name} ({b.start_time.strftime('%H:%M')}-{b.end_time.strftime('%H:%M')})",
            "start": b.start_time.isoformat(),
            "end": b.end_time.isoformat(),
            "event_type": "booking",
            "color": tenant_colors[t_name],
        })

    return events


@router.get("/rooms")
async def workspace_rooms(
    building_id: int = 1,
    date: str = Query(..., description="YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        d = datetime.now()
    day_start = d.replace(hour=0, minute=0, second=0)
    day_end = day_start + timedelta(days=1)

    # Meeting rooms
    res_result = await db.execute(
        select(Resource).where(
            Resource.building_id == building_id,
            Resource.resource_type == ResourceType.meeting_room,
        )
    )
    rooms = res_result.scalars().all()
    room_ids = [r.id for r in rooms]

    # Bookings for the day
    bookings_result = await db.execute(
        select(Booking).where(
            Booking.resource_id.in_(room_ids) if room_ids else Booking.id < 0,
            Booking.start_time < day_end,
            Booking.end_time > day_start,
        )
    )
    bks = bookings_result.scalars().all()

    # Enrich bookings with tenant name
    booking_list = []
    for b in bks:
        tenant = await db.get(Tenant, b.tenant_id) if b.tenant_id else None
        booking_list.append({
            "id": b.id,
            "resource_id": b.resource_id,
            "tenant_id": b.tenant_id,
            "start_time": b.start_time.isoformat(),
            "end_time": b.end_time.isoformat(),
            "tenant_name": tenant.company_name if tenant else None,
        })

    return {
        "rooms": [
            {
                "id": r.id,
                "name": r.name,
                "capacity": r.capacity or 0,
                "rate_coins_per_hour": r.rate_coins_per_hour or 0,
                "rate_money_per_hour": r.rate_money_per_hour or 0,
                "amenities": r.amenities or [],
            }
            for r in rooms
        ],
        "bookings": booking_list,
    }
