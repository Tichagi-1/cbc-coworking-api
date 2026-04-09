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
    MeetingRoom,
    Tenant,
    Unit,
    User,
    UserRole,
)
from app.core.auth import get_current_user, require_role


# Two routers in one file so the file maps to "bookings" feature.
rooms_router = APIRouter(prefix="/meeting-rooms", tags=["meeting-rooms"])
bookings_router = APIRouter(prefix="/bookings", tags=["bookings"])


# ── Schemas ─────────────────────────────────────────────────────────────────

class UnitMini(BaseModel):
    id: int
    name: str
    floor_id: int

    class Config:
        from_attributes = True


class MeetingRoomOut(BaseModel):
    id: int
    unit_id: int
    name: str
    capacity: int
    rate_coins_per_hour: float
    rate_money_per_hour: float
    amenities: list | None
    is_active: bool
    unit: UnitMini | None = None


class MeetingRoomCreate(BaseModel):
    unit_id: int
    name: str
    capacity: int
    rate_coins_per_hour: float
    rate_money_per_hour: float
    amenities: list[str] | None = None


class SlotOut(BaseModel):
    time: str
    available: bool


class BookingOut(BaseModel):
    id: int
    room_id: int
    tenant_id: int
    start_time: datetime
    end_time: datetime
    payment_type: str
    coins_charged: float
    money_charged: float

    class Config:
        from_attributes = True


class BookingCreate(BaseModel):
    room_id: int
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


async def _hydrate_room(db: AsyncSession, room: MeetingRoom) -> MeetingRoomOut:
    unit = await db.get(Unit, room.unit_id)
    return MeetingRoomOut(
        id=room.id,
        unit_id=room.unit_id,
        name=room.name,
        capacity=room.capacity,
        rate_coins_per_hour=room.rate_coins_per_hour,
        rate_money_per_hour=room.rate_money_per_hour,
        amenities=room.amenities,
        is_active=room.is_active,
        unit=(
            UnitMini(id=unit.id, name=unit.name, floor_id=unit.floor_id)
            if unit
            else None
        ),
    )


# ── Meeting rooms ───────────────────────────────────────────────────────────

@rooms_router.get("", response_model=list[MeetingRoomOut])
async def list_meeting_rooms(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(
        select(MeetingRoom).where(MeetingRoom.is_active == True)  # noqa: E712
    )
    rooms = result.scalars().all()
    return [await _hydrate_room(db, r) for r in rooms]


@rooms_router.post("", response_model=MeetingRoomOut)
async def create_meeting_room(
    data: MeetingRoomCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    unit = await db.get(Unit, data.unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")

    room = MeetingRoom(**data.model_dump())
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return await _hydrate_room(db, room)


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

    room = await db.get(MeetingRoom, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    day_start = datetime.combine(d, time(0, 0))
    day_end = day_start + timedelta(days=1)

    bookings_result = await db.execute(
        select(Booking).where(
            Booking.room_id == room_id,
            Booking.start_time < day_end,
            Booking.end_time > day_start,
        )
    )
    existing = bookings_result.scalars().all()

    slots: list[SlotOut] = []
    # 24 slots of 30 minutes from 08:00 to 20:00 (last slot is 19:30–20:00)
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

    # Authorization: tenants can only book for their own tenant record
    if not _is_admin(user):
        my_tenant = await _get_tenant_for_user(db, user)
        if not my_tenant or my_tenant.id != data.tenant_id:
            raise HTTPException(
                status_code=403, detail="Cannot book for another tenant"
            )

    tenant = await db.get(Tenant, data.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    room = await db.get(MeetingRoom, data.room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # Overlap check
    overlap_result = await db.execute(
        select(Booking).where(
            Booking.room_id == data.room_id,
            Booking.start_time < data.end_time,
            Booking.end_time > data.start_time,
        )
    )
    if overlap_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409, detail="Time slot overlaps existing booking"
        )

    coins_needed = duration_hours * room.rate_coins_per_hour
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
            ratio = (
                room.rate_money_per_hour / room.rate_coins_per_hour
                if room.rate_coins_per_hour > 0
                else 0
            )
            money_charged = round(coins_remaining * ratio, 2)
            payment_type = BookingPaymentType.money
    else:
        money_charged = round(duration_hours * room.rate_money_per_hour, 2)
        payment_type = BookingPaymentType.money

    if coins_used > 0:
        tenant.coin_balance = round(tenant.coin_balance - coins_used, 2)
        tx = CoinTransaction(
            tenant_id=tenant.id,
            delta=-coins_used,
            reason=CoinTxReason.booking_debit,
            note=f"Booking {room.name}",
        )
        db.add(tx)

    booking = Booking(
        room_id=data.room_id,
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
    room_id: int | None = None,
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

    if room_id is not None:
        query = query.where(Booking.room_id == room_id)
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

    # Refund coins (reverse the original debit)
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
