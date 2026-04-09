import os
import shutil
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database import get_db
from app.models import (
    Booking,
    Building,
    Floor,
    Lease,
    MeetingRoom,
    Unit,
    Zone,
    User,
    UserRole,
)
from app.core.auth import get_current_user, require_role
from app.config import settings

router = APIRouter(prefix="/buildings", tags=["buildings"])


# ── Schemas ────────────────────────────────────────────────────────────────

class BuildingOut(BaseModel):
    id: int
    name: str
    address: str
    building_class: str
    total_area: float
    leasable_area: float

    class Config:
        from_attributes = True


class BuildingCreate(BaseModel):
    name: str
    address: str
    building_class: str
    total_area: float
    leasable_area: float


class FloorOut(BaseModel):
    id: int
    building_id: int
    number: int
    name: str | None
    floor_plan_url: str | None

    class Config:
        from_attributes = True


class FloorCreate(BaseModel):
    number: int
    name: str | None = None


class FloorPatch(BaseModel):
    name: str | None = None


class ZoneOut(BaseModel):
    id: int
    floor_id: int
    unit_id: int | None
    points: list
    label: str | None
    zone_type: str

    class Config:
        from_attributes = True


class ZoneUpsert(BaseModel):
    unit_id: int | None = None
    points: list
    label: str | None = None
    zone_type: str


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/", response_model=list[BuildingOut])
async def list_buildings(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Building))
    return result.scalars().all()


@router.post("/", response_model=BuildingOut)
async def create_building(
    data: BuildingCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    building = Building(**data.model_dump())
    db.add(building)
    await db.commit()
    await db.refresh(building)
    return building


@router.get("/{building_id}/floors", response_model=list[FloorOut])
async def list_floors(building_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Floor).where(Floor.building_id == building_id))
    return result.scalars().all()


@router.get("/{building_id}/floors/{floor_id}", response_model=FloorOut)
async def get_floor(
    building_id: int,
    floor_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(
        select(Floor).where(Floor.id == floor_id, Floor.building_id == building_id)
    )
    floor = result.scalar_one_or_none()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")
    return floor


@router.post("/{building_id}/floors", response_model=FloorOut)
async def create_floor(
    building_id: int,
    data: FloorCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    building = await db.get(Building, building_id)
    if not building:
        raise HTTPException(status_code=404, detail="Building not found")

    floor = Floor(building_id=building_id, number=data.number, name=data.name)
    db.add(floor)
    await db.commit()
    await db.refresh(floor)
    return floor


@router.patch("/{building_id}/floors/{floor_id}", response_model=FloorOut)
async def update_floor(
    building_id: int,
    floor_id: int,
    data: FloorPatch,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    result = await db.execute(
        select(Floor).where(Floor.id == floor_id, Floor.building_id == building_id)
    )
    floor = result.scalar_one_or_none()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    payload = data.model_dump(exclude_unset=True)
    for k, v in payload.items():
        setattr(floor, k, v)

    await db.commit()
    await db.refresh(floor)
    return floor


@router.delete("/{building_id}/floors/{floor_id}", status_code=204)
async def delete_floor(
    building_id: int,
    floor_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    """
    Cascade-delete a floor and everything that hangs off it. Order is
    important because none of the FKs have ON DELETE CASCADE configured:
        bookings → meeting_rooms → leases → zones → units → floor
    Coin transactions reference bookings only via a non-FK integer
    column, so they're left alone.
    """
    result = await db.execute(
        select(Floor).where(Floor.id == floor_id, Floor.building_id == building_id)
    )
    floor = result.scalar_one_or_none()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    try:
        # Units on this floor
        units_result = await db.execute(
            select(Unit).where(Unit.floor_id == floor_id)
        )
        units = units_result.scalars().all()
        unit_ids = [u.id for u in units]

        if unit_ids:
            # Meeting rooms linked to those units
            mr_result = await db.execute(
                select(MeetingRoom).where(MeetingRoom.unit_id.in_(unit_ids))
            )
            meeting_rooms = mr_result.scalars().all()
            mr_ids = [m.id for m in meeting_rooms]

            # Bookings for those meeting rooms
            if mr_ids:
                bookings_result = await db.execute(
                    select(Booking).where(Booking.room_id.in_(mr_ids))
                )
                for b in bookings_result.scalars().all():
                    await db.delete(b)

            # Meeting rooms themselves
            for mr in meeting_rooms:
                await db.delete(mr)

            # Leases for those units
            leases_result = await db.execute(
                select(Lease).where(Lease.unit_id.in_(unit_ids))
            )
            for lease in leases_result.scalars().all():
                await db.delete(lease)

        # Zones on this floor (also covers any zones with unit_id in unit_ids,
        # since zones live with their floor)
        zones_result = await db.execute(
            select(Zone).where(Zone.floor_id == floor_id)
        )
        for z in zones_result.scalars().all():
            await db.delete(z)

        # Units on the floor
        for u in units:
            await db.delete(u)

        # Finally the floor itself
        await db.delete(floor)
        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Failed to delete floor: {e}"
        )


@router.post("/{building_id}/floors/{floor_id}/plan")
async def upload_floor_plan(
    building_id: int,
    floor_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    """Upload PNG/JPG/PDF floor plan. PDF auto-converted to PNG."""
    result = await db.execute(select(Floor).where(Floor.id == floor_id, Floor.building_id == building_id))
    floor = result.scalar_one_or_none()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    upload_dir = Path(settings.UPLOAD_DIR) / "floor_plans"
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix.lower()
    dest_path = upload_dir / f"floor_{floor_id}{ext}"

    with dest_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # PDF → PNG conversion
    if ext == ".pdf":
        from pdf2image import convert_from_path
        images = convert_from_path(str(dest_path), dpi=150, first_page=1, last_page=1)
        png_path = upload_dir / f"floor_{floor_id}.png"
        images[0].save(str(png_path), "PNG")
        dest_path.unlink()
        dest_path = png_path

    plan_url = f"/static/floor_plans/{dest_path.name}"
    floor.floor_plan_url = plan_url
    await db.commit()

    return {"floor_plan_url": plan_url}


# ── Zone (canvas polygon) endpoints ───────────────────────────────────────

@router.get("/{building_id}/floors/{floor_id}/zones", response_model=list[ZoneOut])
async def get_zones(
    building_id: int,
    floor_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(select(Zone).where(Zone.floor_id == floor_id))
    return result.scalars().all()


@router.get("/{building_id}/floors/{floor_id}/snapshot")
async def floor_snapshot(
    building_id: int,
    floor_id: int,
    date: str = Query(..., description="Snapshot date YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """
    Return zones for a floor with status synthesized from lease history
    as of `date`. A zone is "occupied" if its linked unit has any lease
    where start_date <= date AND end_date >= date, otherwise "vacant".
    Reserved is not derivable from history; only occupied/vacant.
    """
    try:
        snapshot_dt = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    zones_result = await db.execute(select(Zone).where(Zone.floor_id == floor_id))
    zones = zones_result.scalars().all()

    unit_ids = [z.unit_id for z in zones if z.unit_id is not None]
    units_by_id: dict[int, Unit] = {}
    occupied_unit_ids: set[int] = set()

    if unit_ids:
        units_result = await db.execute(select(Unit).where(Unit.id.in_(unit_ids)))
        for u in units_result.scalars().all():
            units_by_id[u.id] = u

        leases_result = await db.execute(
            select(Lease).where(
                Lease.unit_id.in_(unit_ids),
                Lease.start_date <= snapshot_dt,
                Lease.end_date >= snapshot_dt,
            )
        )
        occupied_unit_ids = {l.unit_id for l in leases_result.scalars().all()}

    return [
        {
            "id": z.id,
            "floor_id": z.floor_id,
            "unit_id": z.unit_id,
            "points": z.points,
            "label": z.label or (units_by_id.get(z.unit_id).name if z.unit_id in units_by_id else None),
            "zone_type": z.zone_type,
            "status": (
                "occupied" if z.unit_id and z.unit_id in occupied_unit_ids else "vacant"
            ) if z.unit_id else None,
        }
        for z in zones
    ]


@router.put("/{building_id}/floors/{floor_id}/zones")
async def save_zones(
    building_id: int,
    floor_id: int,
    zones: list[ZoneUpsert],
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    """Replace all zones for a floor (full save from canvas editor)."""
    existing = await db.execute(select(Zone).where(Zone.floor_id == floor_id))
    for z in existing.scalars().all():
        await db.delete(z)

    for z in zones:
        new_zone = Zone(floor_id=floor_id, **z.model_dump())
        db.add(new_zone)

    await db.commit()
    return {"saved": len(zones)}
