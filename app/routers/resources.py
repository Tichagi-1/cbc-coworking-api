from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database import get_db
from app.models import (
    Resource,
    ResourceType,
    UnitStatus,
    Building,
    Floor,
    Zone,
    User,
    UserRole,
)
from app.core.auth import get_current_user, require_role


router = APIRouter(prefix="/resources", tags=["resources"])


# ── Schemas ─────────────────────────────────────────────────────────────────

class ResourceOut(BaseModel):
    id: int
    building_id: int
    floor_id: int | None
    name: str
    resource_type: str
    status: str
    description: str | None = None
    photos: list | None = None
    tenant_name: str | None = None

    area_m2: float | None = None
    seats: int | None = None
    monthly_rate: float | None = None
    rate_period: str | None = None

    capacity: int | None = None
    rate_coins_per_hour: float | None = None
    rate_money_per_hour: float | None = None
    amenities: list | None = None

    rate_per_hour: float | None = None
    is_standalone_bookable: bool = True

    min_advance_minutes: int = 0
    resident_discount_pct: int = 0

    zoho_contract_id: str | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class ResourceCreate(BaseModel):
    building_id: int
    floor_id: int | None = None
    name: str
    resource_type: ResourceType
    status: UnitStatus = UnitStatus.vacant
    description: str | None = None
    photos: list | None = None
    tenant_name: str | None = None

    area_m2: float | None = None
    seats: int | None = None
    monthly_rate: float | None = None
    rate_period: str | None = "month"

    capacity: int | None = None
    rate_coins_per_hour: float | None = None
    rate_money_per_hour: float | None = None
    amenities: list | None = None

    rate_per_hour: float | None = None
    is_standalone_bookable: bool = True

    min_advance_minutes: int = 0
    resident_discount_pct: int = 0


class ResourcePatch(BaseModel):
    floor_id: int | None = None
    name: str | None = None
    resource_type: ResourceType | None = None
    status: UnitStatus | None = None
    description: str | None = None
    photos: list | None = None
    tenant_name: str | None = None

    area_m2: float | None = None
    seats: int | None = None
    monthly_rate: float | None = None
    rate_period: str | None = None

    capacity: int | None = None
    rate_coins_per_hour: float | None = None
    rate_money_per_hour: float | None = None
    amenities: list | None = None

    rate_per_hour: float | None = None
    is_standalone_bookable: bool | None = None

    min_advance_minutes: int | None = None
    resident_discount_pct: int | None = None


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("", response_model=list[ResourceOut])
async def list_resources(
    type: ResourceType | None = None,
    building_id: int | None = None,
    floor_id: int | None = None,
    status: UnitStatus | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    query = select(Resource)
    if type is not None:
        query = query.where(Resource.resource_type == type)
    if building_id is not None:
        query = query.where(Resource.building_id == building_id)
    if floor_id is not None:
        query = query.where(Resource.floor_id == floor_id)
    if status is not None:
        query = query.where(Resource.status == status)
    result = await db.execute(query.order_by(Resource.id))
    return result.scalars().all()


@router.post("", response_model=ResourceOut)
async def create_resource(
    data: ResourceCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    building = await db.get(Building, data.building_id)
    if not building:
        raise HTTPException(status_code=404, detail="Building not found")
    if data.floor_id is not None:
        floor = await db.get(Floor, data.floor_id)
        if not floor or floor.building_id != data.building_id:
            raise HTTPException(
                status_code=400,
                detail="Floor does not belong to building",
            )

    resource = Resource(**data.model_dump())
    db.add(resource)
    await db.commit()
    await db.refresh(resource)
    return resource


@router.get("/{resource_id}", response_model=ResourceOut)
async def get_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    resource = await db.get(Resource, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    return resource


@router.patch("/{resource_id}", response_model=ResourceOut)
async def update_resource(
    resource_id: int,
    data: ResourcePatch,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    resource = await db.get(Resource, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    old_name = resource.name
    payload = data.model_dump(exclude_unset=True)
    for k, v in payload.items():
        setattr(resource, k, v)

    # Propagate name change to zone labels so canvas stays in sync
    if "name" in payload and payload["name"] != old_name:
        zones_result = await db.execute(
            select(Zone).where(Zone.resource_id == resource_id)
        )
        for z in zones_result.scalars().all():
            if z.label == old_name or z.label is None:
                z.label = resource.name

    await db.commit()
    await db.refresh(resource)
    return resource


@router.delete("/{resource_id}", status_code=204)
async def delete_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    resource = await db.get(Resource, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    try:
        # Detach zones (don't cascade delete the polygon — user might want
        # to relink it). Bookings tied to this resource are also detached.
        from app.models import Booking, Zone

        zones_result = await db.execute(
            select(Zone).where(Zone.resource_id == resource_id)
        )
        for z in zones_result.scalars().all():
            z.resource_id = None

        bookings_result = await db.execute(
            select(Booking).where(Booking.resource_id == resource_id)
        )
        for b in bookings_result.scalars().all():
            b.resource_id = None

        await db.delete(resource)
        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Failed to delete resource: {e}"
        )
