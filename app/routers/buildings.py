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
    Resource,
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
    resource_id: int | None = None
    points: list
    label: str | None = None
    # Convenience fields joined from the linked resource so the canvas
    # can render fill/border/label without a second request.
    resource_type: str | None = None
    status: str | None = None

    class Config:
        from_attributes = True


class ZoneUpsert(BaseModel):
    resource_id: int | None = None
    points: list
    label: str | None = None


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
    Cascade-delete a floor and everything that hangs off it. Order:
        bookings → resources → zones → floor
    Resources on this floor are deleted (and any bookings tied to them
    first). Tenants and coin transactions are unaffected.
    """
    result = await db.execute(
        select(Floor).where(Floor.id == floor_id, Floor.building_id == building_id)
    )
    floor = result.scalar_one_or_none()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    try:
        # Resources on this floor
        res_result = await db.execute(
            select(Resource).where(Resource.floor_id == floor_id)
        )
        resources = res_result.scalars().all()
        resource_ids = [r.id for r in resources]

        if resource_ids:
            # Bookings tied to those resources
            bookings_result = await db.execute(
                select(Booking).where(Booking.resource_id.in_(resource_ids))
            )
            for b in bookings_result.scalars().all():
                await db.delete(b)

        # Zones on this floor
        zones_result = await db.execute(
            select(Zone).where(Zone.floor_id == floor_id)
        )
        for z in zones_result.scalars().all():
            await db.delete(z)

        # Resources themselves
        for r in resources:
            await db.delete(r)

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

def _zone_to_out(z: Zone, resource: Resource | None) -> dict:
    return {
        "id": z.id,
        "floor_id": z.floor_id,
        "resource_id": z.resource_id,
        "points": z.points,
        # Resource name takes priority over zone.label — guarantees
        # the canvas shows the current resource name even if zone.label
        # is stale from before a rename.
        "label": (resource.name if resource else None) or z.label,
        "resource_type": resource.resource_type if resource else None,
        "status": resource.status if resource else None,
    }


@router.get("/{building_id}/floors/{floor_id}/zones", response_model=list[ZoneOut])
async def get_zones(
    building_id: int,
    floor_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    zones_result = await db.execute(select(Zone).where(Zone.floor_id == floor_id))
    zones = zones_result.scalars().all()

    resource_ids = [z.resource_id for z in zones if z.resource_id is not None]
    res_by_id: dict[int, Resource] = {}
    if resource_ids:
        res_result = await db.execute(
            select(Resource).where(Resource.id.in_(resource_ids))
        )
        res_by_id = {r.id: r for r in res_result.scalars().all()}

    return [
        _zone_to_out(z, res_by_id.get(z.resource_id) if z.resource_id else None)
        for z in zones
    ]


@router.get("/{building_id}/floors/{floor_id}/snapshot")
async def floor_snapshot(
    building_id: int,
    floor_id: int,
    date: str = Query(..., description="Snapshot date YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """
    Historical zone snapshot. Reads the leases table directly via the
    legacy Lease model (which still references the old units table).
    Resources don't yet have lease history, so for now this returns the
    current resource status; the lease check is preserved as a stub for
    when leases are migrated to reference resources.
    """
    try:
        snapshot_dt = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    zones_result = await db.execute(select(Zone).where(Zone.floor_id == floor_id))
    zones = zones_result.scalars().all()

    resource_ids = [z.resource_id for z in zones if z.resource_id is not None]
    res_by_id: dict[int, Resource] = {}
    if resource_ids:
        res_result = await db.execute(
            select(Resource).where(Resource.id.in_(resource_ids))
        )
        res_by_id = {r.id: r for r in res_result.scalars().all()}

    return [_zone_to_out(z, res_by_id.get(z.resource_id) if z.resource_id else None) for z in zones]


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
