from datetime import datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database import get_db
from app.models import (
    Booking,
    BookingPaymentType,
    CoinTransaction,
    CoinTxReason,
    Resource,
    ResourceType,
    Tenant,
    User,
    UserRole,
)
from app.core.auth import get_current_user, require_role


# /meeting-rooms is now a thin compat wrapper that queries Resource where
# resource_type='meeting_room'. Frontend code that's already in flight may
# still call it; new code should use /resources?type=meeting_room directly.
rooms_router = APIRouter(prefix="/meeting-rooms", tags=["meeting-rooms"])
bookings_router = APIRouter(prefix="/bookings", tags=["bookings"])


# ── Schemas ─────────────────────────────────────────────────────────────────

class ResourceMini(BaseModel):
    id: int
    name: str
    floor_id: int | None

    class Config:
        from_attributes = True


class MeetingRoomOut(BaseModel):
    """
    Compatibility shape — historically returned from /meeting-rooms. We now
    project Resource rows into this shape so existing frontend code keeps
    working during the transition.
    """
    id: int
    unit_id: int  # legacy alias for resource id
    name: str
    capacity: int
    rate_coins_per_hour: float
    rate_money_per_hour: float
    amenities: list | None
    is_active: bool
    unit: ResourceMini | None = None


class SlotOut(BaseModel):
    time: str
    available: bool


class BookingOut(BaseModel):
    id: int
    resource_id: int | None
    tenant_id: int
    start_time: datetime
    end_time: datetime
    payment_type: str
    coins_charged: float
    money_charged: float

    # Legacy alias so existing frontend code that reads `room_id` still works
    @property
    def room_id(self) -> int | None:  # pragma: no cover
        return self.resource_id

    class Config:
        from_attributes = True


class BookingCreate(BaseModel):
    resource_id: int
    tenant_id: int
    start_time: datetime
    end_time: datetime


# ── Helpers ─────────────────────────────────────────────────────────────────

def _hours_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600.0


async def _get_tenant_for_user(db: AsyncSession, user: User) -> Optional[Tenant]:
    result = await db.execute(select(Tenant).where(Tenant.user_id == user.id))
    return result.scalar_one_or_none()


def _is_admin(user: User) -> bool:
    return user.role in (UserRole.admin, UserRole.manager)


def _project_room(r: Resource) -> MeetingRoomOut:
    return MeetingRoomOut(
        id=r.id,
        unit_id=r.id,
        name=r.name,
        capacity=r.capacity or 0,
        rate_coins_per_hour=r.rate_coins_per_hour or 0,
        rate_money_per_hour=r.rate_money_per_hour or 0,
        amenities=r.amenities,
        is_active=True,
        unit=ResourceMini(id=r.id, name=r.name, floor_id=r.floor_id),
    )


# ── /meeting-rooms compat shim ─────────────────────────────────────────────

@rooms_router.get("", response_model=list[MeetingRoomOut])
async def list_meeting_rooms(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(
        select(Resource).where(Resource.resource_type == ResourceType.meeting_room)
    )
    return [_project_room(r) for r in result.scalars().all()]


@rooms_router.get("/{room_id}/availability", response_model=list[SlotOut])
async def room_availability(
    room_id: int,
    date: str = Query(..., description="YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    resource = await db.get(Resource, room_id)
    if not resource or resource.resource_type != ResourceType.meeting_room:
        raise HTTPException(status_code=404, detail="Meeting room not found")

    day_start = datetime.combine(d, time(0, 0))
    day_end = day_start + timedelta(days=1)

    bookings_result = await db.execute(
        select(Booking).where(
            Booking.resource_id == room_id,
            Booking.start_time < day_end,
            Booking.end_time > day_start,
        )
    )
    existing = bookings_result.scalars().all()

    slots: list[SlotOut] = []
    for i in range(24):
        slot_start = datetime.combine(d, time(8, 0)) + timedelta(minutes=30 * i)
        slot_end = slot_start + timedelta(minutes=30)
        available = not any(
            b.start_time < slot_end and b.end_time > slot_start for b in existing
        )
        slots.append(SlotOut(time=slot_start.strftime("%H:%M"), available=available))

    return slots


# ── Bookings ────────────────────────────────────────────────────────────────

@bookings_router.post("", response_model=BookingOut)
async def create_booking(
    data: BookingCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if data.end_time <= data.start_time:
        raise HTTPException(
            status_code=400, detail="end_time must be after start_time"
        )

    duration_hours = _hours_between(data.start_time, data.end_time)
    if duration_hours <= 0:
        raise HTTPException(status_code=400, detail="duration must be positive")

    if not _is_admin(user):
        my_tenant = await _get_tenant_for_user(db, user)
        if not my_tenant or my_tenant.id != data.tenant_id:
            raise HTTPException(
                status_code=403, detail="Cannot book for another tenant"
            )

    tenant = await db.get(Tenant, data.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    resource = await db.get(Resource, data.resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    coins_rate = resource.rate_coins_per_hour or 0
    money_rate = resource.rate_money_per_hour or 0

    # Overlap check on the resource
    overlap_result = await db.execute(
        select(Booking).where(
            Booking.resource_id == data.resource_id,
            Booking.start_time < data.end_time,
            Booking.end_time > data.start_time,
        )
    )
    if overlap_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409, detail="Time slot overlaps existing booking"
        )

    coins_needed = duration_hours * coins_rate
    coins_used = 0.0
    money_charged = 0.0
    payment_type = BookingPaymentType.money

    if tenant.is_resident:
        if tenant.coin_balance >= coins_needed:
            coins_used = coins_needed
            payment_type = BookingPaymentType.coins
        else:
            coins_used = tenant.coin_balance
            coins_remaining = coins_needed - coins_used
            ratio = (money_rate / coins_rate) if coins_rate > 0 else 0
            money_charged = round(coins_remaining * ratio, 2)
            payment_type = BookingPaymentType.money
    else:
        money_charged = round(duration_hours * money_rate, 2)
        payment_type = BookingPaymentType.money

    if coins_used > 0:
        tenant.coin_balance = round(tenant.coin_balance - coins_used, 2)
        tx = CoinTransaction(
            tenant_id=tenant.id,
            delta=-coins_used,
            reason=CoinTxReason.booking_debit,
            note=f"Booking {resource.name}",
        )
        db.add(tx)

    booking = Booking(
        resource_id=data.resource_id,
        tenant_id=data.tenant_id,
        start_time=data.start_time,
        end_time=data.end_time,
        payment_type=payment_type,
        coins_charged=coins_used,
        money_charged=money_charged,
        source="web",
    )
    db.add(booking)
    await db.commit()
    await db.refresh(booking)
    return booking


@bookings_router.get("", response_model=list[BookingOut])
async def list_bookings(
    resource_id: int | None = None,
    tenant_id: int | None = None,
    date: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(Booking)

    if not _is_admin(user):
        my_tenant = await _get_tenant_for_user(db, user)
        if not my_tenant:
            return []
        query = query.where(Booking.tenant_id == my_tenant.id)

    if resource_id is not None:
        query = query.where(Booking.resource_id == resource_id)
    if tenant_id is not None:
        query = query.where(Booking.tenant_id == tenant_id)
    if date:
        try:
            d = datetime.strptime(date, "%Y-%m-%d").date()
            day_start = datetime.combine(d, time(0, 0))
            day_end = day_start + timedelta(days=1)
            query = query.where(
                Booking.start_time < day_end, Booking.end_time > day_start
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    result = await db.execute(query.order_by(Booking.start_time))
    return result.scalars().all()


@bookings_router.delete("/{booking_id}")
async def cancel_booking(
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    booking = await db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if not _is_admin(user):
        my_tenant = await _get_tenant_for_user(db, user)
        if not my_tenant or booking.tenant_id != my_tenant.id:
            raise HTTPException(
                status_code=403, detail="Cannot cancel another tenant's booking"
            )

    if booking.coins_charged > 0:
        tenant = await db.get(Tenant, booking.tenant_id)
        if tenant:
            tenant.coin_balance = round(
                tenant.coin_balance + booking.coins_charged, 2
            )
            tx = CoinTransaction(
                tenant_id=tenant.id,
                delta=booking.coins_charged,
                reason=CoinTxReason.refund,
                reference_id=booking_id,
                note="Booking cancellation",
            )
            db.add(tx)

    await db.delete(booking)
    await db.commit()
    return {"deleted": booking_id}
